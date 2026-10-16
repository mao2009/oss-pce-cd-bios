"""Consumer of the signed CD_READ verdict (Issue #153; schema: docs/verdict-schema.md; producer: #156).

The verdict is produced and attested inside the trusted main CI job. This module never accepts gh output, run
records or branch rules from its caller: it runs `gh attestation verify` itself, recomputes the verdict digest and
the ROM digest, and checks the certificate identity, the commit (this checkout's HEAD), the pins and the validity
window. Any doubt is BLOCKED, and the result starts from cd_read_contract.compat_status() (every declared VERIFIED
claim BLOCKED). Only T1-T5 can be promoted; T6-T8 and the callee ABI (#139) are never touched.

VERIFIED_PATH_ENABLED is False: protect-main requires 0 approvals (#124, plan C until plan A), so no input reaches
VERIFIED in production. Flip it only in a PR reviewed by an independent human approver.
"""
import argparse
import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cd_read_contract as cdr  # noqa: E402
import evidence_provenance as ep  # noqa: E402
from provenance_guard import load_denylist  # noqa: E402

VERIFIED_PATH_ENABLED = False
SCHEMA = "oss-pce-cd-bios/cdread-verdict/v1"
ISSUER = "https://token.actions.githubusercontent.com"
PREDICATE = "https://slsa.dev/provenance/v1"
MAX_VERDICT_BYTES = 65536
MAX_LIFETIME = datetime.timedelta(days=30)
EXIT_BLOCKED = cdr.EXIT_BLOCKED
PROMOTABLE = ("T1", "T2", "T3", "T4", "T5")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
FIELDS = {"schema", "repository", "commit", "ref", "producer", "issued_at", "expires_at", "geargrafx", "evidence",
          "provenance", "main_rules", "claims"}


def _gh(args):  # tests replace this; production runs the real gh
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _head():
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=cdr.ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


def _pairs(pairs):
    keys = [k for k, _ in pairs]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(name):
    raise ValueError(f"non-finite number {name}")


def _is_int(value):
    return type(value) is int


def _exact(obj, keys):
    return type(obj) is dict and set(obj) == set(keys)


def _time(value):
    if type(value) is not str or not TIME.fullmatch(value):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def content_findings(v, commit, rom_bytes):
    """Schema v1 rules (docs/verdict-schema.md section 2), plus commit and ROM correspondence."""
    if not _exact(v, FIELDS):
        return ["verdict is not an object with exactly the v1 fields"]
    hex64 = ep.HEX64.fullmatch
    errors = []
    if v["schema"] != SCHEMA:
        errors.append("schema is not v1")
    if v["repository"] != cdr.REPOSITORY:
        errors.append("repository is not mao2009/oss-pce-cd-bios")
    if type(v["commit"]) is not str or not ep.HEX40.fullmatch(v["commit"]) or v["commit"] != commit:
        errors.append("commit differs from this checkout's HEAD")
    if v["ref"] != ep.TRUSTED_REF:
        errors.append("ref is not refs/heads/main")
    p = v["producer"]
    if not (_exact(p, ("workflow", "run_id", "run_attempt")) and p["workflow"] == ep.SIGNER_WORKFLOW
            and _is_int(p["run_id"]) and _is_int(p["run_attempt"]) and p["run_id"] > 0 and p["run_attempt"] > 0):
        errors.append("producer is not api-source.yml with positive integer run_id/run_attempt")
    g = v["geargrafx"]
    if not (_exact(g, ("revision", "source_archive_sha256")) and g["revision"] == cdr.PINNED["revision"]
            and g["source_archive_sha256"] == cdr.PINNED["sha256"]):
        errors.append("geargrafx differs from the tools/dependencies.json pin")
    e = v["evidence"]
    keys = ("artifact_sha256", "rom_sha256", "bin_sha256", "cue_sha256")
    if not (_exact(e, keys) and all(type(e[k]) is str and hex64(e[k]) for k in keys)):
        errors.append("evidence hashes are not exactly four lowercase SHA-256 values")
    elif not isinstance(rom_bytes, bytes) or hashlib.sha256(rom_bytes).hexdigest() != e["rom_sha256"]:
        errors.append("recomputed ROM sha256 does not match evidence.rom_sha256")
    elif e["rom_sha256"] in load_denylist(cdr.ROOT):
        errors.append("ROM sha256 is on the denylist")
    if v["provenance"] != {"result": ep.PASS, "verifier": "tools/evidence_provenance.py"}:
        errors.append("provenance is not a #125 PASS")
    r = v["main_rules"]
    if not (_exact(r, ("source", "required_approving_review_count")) and r["source"] == "github-api"
            and _is_int(r["required_approving_review_count"])):
        errors.append("main_rules malformed")
    elif r["required_approving_review_count"] < 1:
        errors.append("main requires fewer than 1 approving review (protect-main plan A not in place)")
    c, declared = v["claims"], {claim["id"]: claim["status"] for claim in cdr.TRANSPORT_CLAIMS}
    if not (_exact(c, declared) and all(c[k] in ("VERIFIED", "BLOCKED") and type(c[k]) is str for k in PROMOTABLE)
            and all(c[k] == declared[k] and type(c[k]) is str for k in declared if k not in PROMOTABLE)):
        errors.append("claims must be exactly T1-T8, T1-T5 VERIFIED/BLOCKED, T6-T8 as declared in the ledger")
    issued, expires, now = _time(v["issued_at"]), _time(v["expires_at"]), _now()
    if issued is None or expires is None or not issued < expires <= issued + MAX_LIFETIME:
        errors.append("issued_at/expires_at malformed or lifetime not in (0, 30 days]")
    elif not issued <= now < expires:
        errors.append("verdict is expired or not yet valid")
    return errors


def signature_findings(verdict_bytes, commit, producer, bundle_path):
    """Run gh ourselves on the exact bytes, then re-check its output; never trust a caller's gh JSON."""
    repo = cdr.REPOSITORY
    with tempfile.TemporaryDirectory(prefix="verdict-") as tmp:
        path = Path(tmp) / "cdread-verdict.json"
        path.write_bytes(verdict_bytes)
        args = ["attestation", "verify", str(path), "--repo", repo, "--signer-workflow", f"{repo}/{ep.SIGNER_WORKFLOW}",
                "--source-ref", ep.TRUSTED_REF, "--source-digest", commit, "--cert-oidc-issuer", ISSUER,
                "--predicate-type", PREDICATE, "--deny-self-hosted-runners", "--format", "json"]
        if bundle_path is not None:
            args += ["--bundle", str(bundle_path)]
        try:
            code, out = _gh(args)
            results = ep._statements(json.loads(out)) if code == 0 else None
        except Exception as error:  # gh missing, timeout, bad JSON: fail closed
            return [f"gh attestation verify could not run: {type(error).__name__}"]
    if code != 0:
        return [f"gh attestation verify failed (exit {code})"]
    if not results:
        return ["gh attestation verify returned no verification result"]
    digest = hashlib.sha256(verdict_bytes).hexdigest()
    matched = [res for res in results if digest in ep._subject_digests(res)]
    if not matched:
        return ["no attestation has this verdict's recomputed sha256 as subject (tampered or another file)"]
    policy = {"repository": repo, "commit": commit, "signer_workflow": ep.SIGNER_WORKFLOW}
    run = {"id": producer["run_id"], "run_attempt": producer["run_attempt"], "head_sha": commit,
           "head_branch": "main", "event": "push", "path": ep.SIGNER_WORKFLOW,
           "repository": {"full_name": repo}, "head_repository": {"full_name": repo}}
    findings = []
    for res in matched:
        f = ep.certificate_findings(res, policy) + ep.run_findings(res, run, policy)
        if ep._get(res, "signature", "certificate", "issuer") != ISSUER:
            f.append("certificate issuer is not GitHub Actions OIDC")
        if not f:
            return []
        findings = findings or f
    return findings


def consume(verdict_bytes, rom_bytes, bundle_path=None):
    """(status per claim id, reasons). Reasons empty only when T1-T5 were promoted from the signed verdict."""
    status = cdr.compat_status()
    if not isinstance(verdict_bytes, bytes) or len(verdict_bytes) > MAX_VERDICT_BYTES:
        return status, ["verdict missing, not bytes, or larger than 65536 bytes"]
    try:
        v = json.loads(verdict_bytes.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
        commit = _head()
    except (UnicodeDecodeError, ValueError, RecursionError, OSError, subprocess.CalledProcessError) as error:
        return status, [f"verdict unreadable: {type(error).__name__}"]
    reasons = content_findings(v, commit, rom_bytes)
    if not reasons:
        reasons = signature_findings(verdict_bytes, commit, v["producer"], bundle_path)
    if not reasons and not VERIFIED_PATH_ENABLED:
        reasons = ["VERIFIED_PATH_ENABLED is False: no independent approval on protect-main yet (#124)"]
    if reasons:
        return status, reasons
    for claim in cdr.TRANSPORT_CLAIMS:
        if claim["id"] in PROMOTABLE and claim["status"] == "VERIFIED" and v["claims"][claim["id"]] == "VERIFIED":
            status[claim["id"]] = "VERIFIED"
    return status, []


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("verdict")
    parser.add_argument("--rom", required=True)
    parser.add_argument("--bundle")
    args = parser.parse_args(argv)
    try:
        verdict_bytes, rom_bytes = Path(args.verdict).read_bytes(), Path(args.rom).read_bytes()
    except OSError as error:
        print(f"BLOCKED CD_READ verdict: unreadable input ({error})")
        return EXIT_BLOCKED
    status, reasons = consume(verdict_bytes, rom_bytes, args.bundle)
    verified = "VERIFIED" in status.values()
    label = "VERIFIED (emulator transport evidence only)" if verified else "BLOCKED"
    print(f"{label} CD_READ verdict: {sorted(status.items())}; reasons: {reasons}")
    return 0 if verified else EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
