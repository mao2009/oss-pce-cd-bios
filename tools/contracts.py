"""Validate research bookkeeping, never certify a BIOS implementation."""
import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {'arguments', 'returns', 'clobbered', 'preserved', 'stack', 'mpr',
          'ram', 'flags', 'irq', 'errors', 'synchronization'}
# Contract fields whose status describes callee ABI behavior: these can only be
# known by executing the callee, so OBSERVED status on them additionally
# requires an executed callee (executed_callee=True). Verification is NOT_RUN
# for every slot, so no contract claim may currently reach OBSERVED.
#
# Low-level hardware observations (e.g. hw_bus_reset: a $1804 register/bus
# fact documented in docs/cd-reset-contract.md) are NOT callee behavior and
# are not contract fields: they never require an executed callee. Hardware
# observation alone must never promote callee ABI claims (returns/flags/irq/
# errors/synchronization/...) to OBSERVED.
CALLEE_BEHAVIOR_FIELDS = frozenset(FIELDS)
# Explicit allowlist of hardware-observation field names. Any field name in
# neither set is rejected, so an unknown name cannot bypass the executed_callee
# check by not being listed as callee behavior.
HARDWARE_OBSERVATION_FIELDS = frozenset({'hw_bus_reset'})
# LIMIT: adjudicate_claim() can accept a hardware observation, but the formal
# API contract ledger (spec/api_contracts.json, validate_contracts) only stores
# CALLEE_BEHAVIOR_FIELDS per slot. A hardware observation therefore has no
# storage in the ledger; validate_contracts is intentionally NOT relaxed.
# A dedicated hardware-observation store needs its own Issue.
# Adjudication categories for every contract claim. VERIFIED is deliberately
# absent: it can only be earned by an executed reference-ROM callee, which no
# slot has (verification stays NOT_RUN / executed_callee false).
CLAIM_STATUSES = {'UNKNOWN', 'HYPOTHESIS', 'OBSERVED', 'BLOCKED'}
EMULATOR_SOURCE_KIND = 'EMULATOR_SOURCE'

# Evidence-entry schema (shape only). A source entry (repository/revision/path/url/sha256)
# only proves that source text exists; it is not execution evidence. An OBSERVED
# claim must additionally reference non-empty, well-formed evidence describing the
# execution conditions, the emulator core revision claimed for the run,
# the input type fed to the callee, the verification result and a sha256 evidence
# hash. Source references alone are not sufficient for OBSERVED.
EVIDENCE_FIELDS = ('execution_conditions', 'core_revision', 'input_type',
                   'verification_result', 'evidence_hash')
EVIDENCE_HASH_RE = re.compile(r'[0-9a-f]{64}\Z')
# Trust level of evidence entries: FORMAT_ONLY. validate_evidence checks shape
# only. Nothing here resolves evidence_hash to a stored artifact or
# checks core_revision against a pin, so a well-formed hash is NOT proof that the
# run happened. Do not read an accepted OBSERVED claim as VERIFIED.
EVIDENCE_TRUST_LEVEL = 'FORMAT_ONLY'
# Synthetic fixtures and placeholders carry this marker in their text. adjudicate_claim() refuses OBSERVED for them.
SYNTHETIC_MARKER = 'SYNTHETIC'
# Positive provenance marker: an OBSERVED evidence entry must say in execution_conditions that it came from the real
# core. Declaration only (no artifact content is read here); absence fails closed.
REAL_CORE_MARKER = 'REALCORE'


# Explicit confusables for the Latin letters used in the markers (S Y N T H E I C R A L O), taken from the
# Cyrillic and Greek look-alikes that UTS #39 maps to them. Not exhaustive: other scripts are a remaining gap.
_CONFUSABLES_TO_LATIN = str.maketrans({
    # Cyrillic look-alikes of the marker letters (S Y N T H E I C A O)
    'Ѕ': 'S', 'ѕ': 'S', 'У': 'Y', 'у': 'Y', 'Ү': 'Y', 'Н': 'H', 'Т': 'T', 'Е': 'E', 'е': 'E',
    'І': 'I', 'і': 'I', 'С': 'C', 'с': 'C', 'А': 'A', 'а': 'A', 'О': 'O', 'о': 'O',
    # Greek look-alikes of the marker letters (A E H I N O T Y)
    'Α': 'A', 'α': 'A', 'Ε': 'E', 'ε': 'E', 'Η': 'H', 'Ι': 'I', 'ι': 'I', 'Ν': 'N',
    'Ο': 'O', 'ο': 'O', 'Τ': 'T', 'Υ': 'Y',
})


def _marker_text(text):
    # Marker comparison form: NFKD with combining marks (Mn/Me) removed, NFKC, confusables folded to Latin,
    # case-folded, all whitespace/punctuation/underscore removed, so 'S Y N T H E T I C', 'syn-thetic',
    # 'Synthetic', 'SÝNTHETIC' and Cyrillic/Greek look-alikes all match the marker.
    decomposed = unicodedata.normalize('NFKD', text)
    base = ''.join(ch for ch in decomposed if unicodedata.category(ch) not in ('Mn', 'Me'))
    folded = unicodedata.normalize('NFKC', base).translate(_CONFUSABLES_TO_LATIN)
    return re.sub(r'[\W_]', '', folded).casefold()


def _has_marker(marker, texts):
    # Field-splitting guard: True when the marker occurs across the texts in order, each field contributing one
    # contiguous piece and other fields (e.g. core_revision) allowed in between. Plain concatenation is not
    # enough: 'SYN' + <hex core_revision> + 'THETIC' would pass it.
    target = _marker_text(marker)
    reached = {0}  # marker prefix lengths matched so far
    for text in texts:
        piece = _marker_text(text)
        new = set()
        for k in reached:
            for start in range(len(piece)):
                n = 0
                while k + n < len(target) and start + n < len(piece) and piece[start + n] == target[k + n]:
                    n += 1
                    new.add(k + n)
        reached |= new
    return len(target) in reached


# Declaration-level fail-closed text rule (Issue #359 round 3). Folding look-alikes into Latin cannot close
# every script (Cyrillic H/I/palochka, Cherokee, dotless i, ... are outside the table), so OBSERVED no longer
# depends on folding alone:
#   (1) every text the adjudicator sees must be pure ASCII, raw and after NFKC; any other codepoint is refused;
#   (2) no single field may contain a run of >= 5 case-folded marker letters (a 5-letter substring of 'synthetic');
#   (3) no field may be a whole fragment of the marker (any length >= 1, e.g. 'SYN', 'THE', 'C').
# Soundness for splits in any order: a marker occurrence in the joined fields is a contiguous 9-letter run.
# An edge field contributes at most 4 of those letters (rule 2) and an interior field lies wholly inside the
# marker (rule 3, refused). Two edge fields give at most 8 < 9, so an occurrence cannot form without a refused field.
# Why 5, not 4: a 4-letter run is 'nthe' in ordinary 'in the' / 'on the' text, which would refuse most real
# execution_conditions prose. 5-letter runs are 'synth', 'ynthe', 'nthet', 'theti', 'hetic'.
# False-positive cost: adjudicated text containing one of those 5-letter runs ('synthesis', 'pathetic', 'sympathetic')
# is refused for OBSERVED, and a field that is a whole fragment of the marker ('the', 'tic', 'syn') is refused.
# Non-ASCII text (even legitimate) is refused for OBSERVED.
# Declaration-level only: a caller can still supply REALCORE and a hash it chose; nothing here authenticates them.
def _is_pure_ascii(text):
    return text.isascii() and unicodedata.normalize('NFKC', text).isascii()


def _leaf_strings(obj):
    # Every string under a claim (keys, values, nested lists such as sources and evidence entries).
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [text for key, value in obj.items() for text in _leaf_strings(key) + _leaf_strings(value)]
    if isinstance(obj, (list, tuple)):
        return [text for value in obj for text in _leaf_strings(value)]
    return []


def _marker_fragment_refused(texts):
    # True when one field has a 5+ letter run of the marker, or is wholly a fragment of it (rules 2 and 3 above).
    target = _marker_text(SYNTHETIC_MARKER)
    for text in texts:
        piece = _marker_text(text)
        if piece and (piece in target or any(piece[i:i + 5] in target for i in range(len(piece) - 4))):
            return True
    return False


# Source-kind semantics (Issue #209 / MEDIUM-1). A source kind classifies the
# provenance of a *source-code reference* only. It never proves that anything was
# executed: execution evidence is the separate `evidence` entries (Issue #208),
# and callee execution is the separate `executed_callee` flag.
#
#   CALLER_SOURCE:   caller 側(HuCC SDK 等)のソース。caller の期待のみ。callee の実行を保証しない。
#   HLE_SOURCE:      HLE エミュレータ(Hu-Go 等)のソース。実機ファームウェアの証拠ではない。
#   EMULATOR_SOURCE: 実コア(Geargrafx ピン rev)のソース参照。実行証跡ではない。
#       OBSERVED の必要条件(1つ以上)だが十分条件ではない: 非空の evidence entries が別途必要で、
#       callee ABI は executed_callee も必要。HYPOTHESIS から OBSERVED への暗黙昇格は無い。
SOURCE_KIND_MEANINGS = {
    'CALLER_SOURCE': 'caller 側ソース。caller の期待のみで callee 実行を保証しない。',
    'HLE_SOURCE': 'HLE エミュレータのソース。実機ファームウェアの証拠ではない。',
    'EMULATOR_SOURCE': '実コアのソース参照。実行証跡ではなく、単独では OBSERVED の十分条件でない。',
}
SOURCE_KINDS = frozenset(SOURCE_KIND_MEANINGS)
CLAIM_KEYS = frozenset({'status', 'value', 'sources', 'confidence', 'registers', 'evidence',
                        'observation_kind'})
# Issue #225: every OBSERVED claim must declare which kind of observation its evidence is, and the
# kind must cover the claim's field. Enforced in adjudicate_claim() (the formal path used by
# validate_contracts) so no wrapper can skip it. The kind is a declaration, not a measurement:
# the rule rejects a kind that does not cover the field, but it does not verify where the evidence
# came from, so a caller can still declare CALLEE_EXECUTION for a hardware readback.
OBSERVATION_KIND_KEY = 'observation_kind'
OBSERVATION_KIND_FIELDS = {
    # Low-level bus/register fact only; never callee ABI (returns/flags/irq/...).
    'HW_BUS_READBACK': HARDWARE_OBSERVATION_FIELDS,
    # Behavior of an executed callee; still needs executed_callee=True in adjudicate_claim.
    'CALLEE_EXECUTION': CALLEE_BEHAVIOR_FIELDS,
}


def validate_contracts(data, inventory, inventory_text):
    """Return normally for consistent unexecuted research; raise ValueError otherwise.

    This schema deliberately cannot represent VERIFIED. OBSERVED promotion
    additionally requires non-empty, format-valid evidence entries (FORMAT_ONLY; not authenticated); source
    code references alone are not execution evidence.
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(isinstance(data, dict) and data.get('schema_version') == 1,
            'unsupported contract schema')
    require(data.get('inventory') == 'spec/api_slots.json' and
            data.get('source_inventory') == 'docs/api-inventory.md', 'inventory references')
    require(isinstance(data.get('target'), str) and data['target'], 'target required')
    require(inventory.get('schema_version') == 1, 'unsupported inventory schema')
    slots = inventory.get('slots', [])
    require(len(slots) == 81 and [s.get('id') for s in slots] == list(range(81)),
            'inventory must contain 81 ordered slots')
    sources = data.get('sources')
    require(isinstance(sources, dict) and sources, 'sources required')
    for key, source in sources.items():
        require(isinstance(source, dict), f'{key}: source object required')
        require(all(isinstance(source.get(f), str) and source[f]
                    for f in ('repository', 'revision', 'path', 'url', 'sha256', 'kind')),
                f'{key}: incomplete source')
        require(re.fullmatch(r'[0-9a-f]{40}', source['revision']) is not None and
                re.fullmatch(r'[0-9a-f]{64}', source['sha256']) is not None,
                f'{key}: source pin/hash invalid')
        require(source['repository'].startswith('https://github.com/') and
                source['url'] == f"{source['repository']}/blob/{source['revision']}/{source['path']}",
                f'{key}: source URL must match pin/path')
        require(source['kind'] in SOURCE_KINDS and
                source.get('provenance_status') == 'PENDING', f'{key}: invalid source status')

    def refs(value, context, required=False):
        require(isinstance(value, list) and all(isinstance(x, str) for x in value),
                f'{context}: source references must be a list')
        require(len(value) == len(set(value)) and all(x in sources for x in value),
                f'{context}: invalid source reference')
        require(not required or bool(value), f'{context}: source required')

    md_rows = re.findall(r'^\| `\$([0-9A-F]{2})` \| `\$([0-9A-F]{4})` \| `([^`]+)`',
                         inventory_text, re.MULTILINE)
    require(len(md_rows) == 81, 'Markdown inventory must contain 81 main rows')
    apis = data.get('apis')
    require(isinstance(apis, list) and len(apis) == 81, 'contracts must contain 81 APIs')
    for i, (api, slot, md) in enumerate(zip(apis, slots, md_rows)):
        require(isinstance(api, dict), f'slot {i}: API object required')
        require(type(api.get('slot')) is int and api['slot'] == i and
                api.get('candidate_name') == slot.get('candidate_name'), f'slot {i}: inventory mismatch')
        entry = f'0x{0xE000 + 3*i:04X}'
        require(api.get('candidate_entry') == entry and int(md[0], 16) == i and
                '0x'+md[1] == entry and md[2] == api['candidate_name'], f'slot {i}: entry/name mismatch')
        require(api.get('mapping_status') == 'HYPOTHESIS' and
                api.get('provenance_status') == 'PENDING' and
                api.get('implementation_status') == 'NOT_STARTED', f'slot {i}: invalid research status')
        require(api.get('confidence') in {'UNKNOWN', 'SOURCE_SUPPORTED'}, f'slot {i}: confidence')
        require(api.get('verification') == {'status': 'NOT_RUN', 'executed_callee': False, 'evidence': []},
                f'slot {i}: no executed callee; verification must be NOT_RUN')
        refs(api.get('sources'), f'slot {i}')
        refs(api.get('mapping_sources'), f'slot {i} mapping')
        require(set(api['mapping_sources']) <= set(api['sources']), f'slot {i}: mapping sources missing')
        observations = api.get('name_observations')
        require(isinstance(observations, list), f'slot {i}: name observations required')
        for obs in observations:
            require(isinstance(obs, dict) and isinstance(obs.get('name'), str) and obs['name'] and
                    isinstance(obs.get('locator'), str) and obs['locator'] and
                    obs.get('status') == 'HYPOTHESIS' and obs.get('entry') in {entry, 'UNKNOWN'},
                    f'slot {i}: invalid name observation')
            refs([obs.get('source')], f'slot {i} name', True)
            require(obs['source'] in api['sources'], f'slot {i}: observation source missing')
        contract = api.get('contract')
        require(isinstance(contract, dict) and set(contract) == FIELDS, f'slot {i}: contract fields')
        source_kinds = {key: source['kind'] for key, source in sources.items()}
        executed_callee = bool(api.get('verification', {}).get('executed_callee'))
        for field, claim in contract.items():
            context = f'slot {i} {field}'
            require(isinstance(claim, dict) and set(claim) <=
                    CLAIM_KEYS, f'{context}: claim fields')
            adjudicate_claim(claim, source_kinds, field=field, context=context,
                             executed_callee=executed_callee)
            refs(claim.get('sources'), context,
                 claim['status'] in {'HYPOTHESIS', 'OBSERVED'})
            if 'registers' in claim:
                registers = claim['registers']
                require(field in {'preserved', 'clobbered'} and isinstance(registers, list) and
                        all(r in {'A', 'X', 'Y', 'S', 'P'} for r in registers) and
                        len(registers) == len(set(registers)), f'{context}: register list invalid')
        preserved = contract['preserved']
        clobbered = contract['clobbered']
        require(not (set(preserved.get('registers', [])) & set(clobbered.get('registers', []))),
                f'slot {i}: contradictory preserved/clobbered registers')
        require(not (preserved['status'] != 'UNKNOWN' and clobbered['status'] != 'UNKNOWN' and
                     preserved['value'] == clobbered['value']), f'slot {i}: contradictory state claims')


def validate_evidence(evidence, context):
    """Validate one claim's evidence-entry structure (shape only).

    Each entry must name the execution conditions, the emulator core revision
    claimed for the run, the input type fed to the callee, the
    verification result and a 64-hex lowercase sha256 evidence hash.
    Trust level is EVIDENCE_TRUST_LEVEL (FORMAT_ONLY): shape is checked, the
    hash is not resolved to a stored artifact, so acceptance does not
    prove authenticity. It also does NOT check that the evidence supports the
    claim's contract field (e.g. nothing ties a $1804 readback entry to `stack`
    or `returns`); field/evidence semantic correspondence is unverified.
    Returns None when consistent, raises ValueError otherwise.
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    require(isinstance(evidence, list), f'{context}: claim evidence must be a list')
    for entry in evidence:
        require(isinstance(entry, dict) and set(entry) == set(EVIDENCE_FIELDS),
                f'{context}: evidence entry must contain exactly {sorted(EVIDENCE_FIELDS)}')
        require(all(isinstance(entry[field], str) and entry[field].strip() for field in EVIDENCE_FIELDS),
                f'{context}: evidence entry values must be non-empty strings')
        require(re.fullmatch(r'[0-9a-f]{40}', entry['core_revision']) is not None,
                f'{context}: core_revision must be a 40-hex lowercase git revision')
        require(EVIDENCE_HASH_RE.fullmatch(entry['evidence_hash']) is not None and
                len(set(entry['evidence_hash'])) > 1,
                f'{context}: evidence_hash must be a 64-hex lowercase sha256 digest')


# Artifact resolution (Issue #224). validate_evidence() stays FORMAT_ONLY and is NOT replaced: resolve_evidence()
# is an explicit, separate step. The entry's evidence_hash is the file name of a synthetic artifact in
# EVIDENCE_ARTIFACT_DIR (content-addressed, so no path is built from other entry text). The artifact's SHA-256 is
# recomputed and must equal that name. core_revision is the ledger-declared string and is only compared with the
# pinned Geargrafx revision; the artifact file carries no revision, so nothing here shows which revision produced the
# bytes. This proves the bytes match the recorded hash, not what they are or who produced them: producer
# authentication is attestation (Issue #124 / #171). Not yet wired into validate_contracts (no OBSERVED claim exists).
EVIDENCE_ARTIFACT_DIR = ROOT / 'build' / 'evidence-artifacts'
ARTIFACT_TRUST_LEVEL = 'ARTIFACT_HASH_RESOLVED'
PINNED_CORE_REVISION = json.loads((ROOT / 'tools/dependencies.json').read_text(encoding='utf-8'))['geargrafx']['revision']


def resolve_evidence(entry, artifact_dir=EVIDENCE_ARTIFACT_DIR, pinned_revision=PINNED_CORE_REVISION):
    """Resolve one evidence entry to its local artifact and check it. Returns the artifact Path.

    Fail closed: raises ValueError for malformed entries, a revision other than the pin, a missing or
    symlinked artifact, and any artifact whose recomputed SHA-256 differs from evidence_hash (altered
    or substituted file). core_revision is checked as the declared string against the pin only; the artifact
    does not carry a revision. Acceptance means hash-and-pin consistency only, not authenticity.
    """
    validate_evidence([entry], 'evidence')
    if entry['core_revision'] != pinned_revision:
        raise ValueError(f"core_revision {entry['core_revision']} differs from pinned {pinned_revision}")
    path = Path(artifact_dir) / entry['evidence_hash']
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"evidence artifact missing or not a regular file: {entry['evidence_hash']}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != entry['evidence_hash']:
        raise ValueError('evidence artifact sha256 does not match evidence_hash (altered or different file)')
    return path


def is_synthetic_evidence(entry):
    """True when any text field of an evidence entry carries the SYNTHETIC marker (fixtures, placeholders)."""
    return _has_marker(SYNTHETIC_MARKER, [entry[field] for field in EVIDENCE_FIELDS])


def adjudicate_claim(claim, source_kinds, *, field, context, executed_callee=False,
                     resolved_evidence_hashes=frozenset()):
    """Pure fail-closed adjudication of one contract claim's classification.

    Deterministic and side-effect free: synthetic claim dicts can be passed
    directly from host tests. Returns None when consistent, raises ValueError
    otherwise. This is the classification rule behind OBSERVED/HYPOTHESIS/
    UNKNOWN/BLOCKED; it rejects any status outside the set (including VERIFIED)
    so an unexecuted callee can never be promoted.

    Source-kind semantics: see SOURCE_KIND_MEANINGS (source references, not execution proof).
    An OBSERVED claim must declare observation_kind (OBSERVATION_KIND_FIELDS) covering its field;
    a non-OBSERVED claim must not declare it.

    Declaration-level text rule (Issue #359 round 3): an OBSERVED claim's strings must be pure ASCII and carry
    no SYNTHETIC fragment in any single field (see the comment above _marker_fragment_refused).

    Real-core provenance: resolved_evidence_hashes is the set of evidence_hash values that
    resolve_evidence() accepted (pinned core revision + artifact sha256). An OBSERVED claim is refused
    unless every evidence entry is non-SYNTHETIC and its hash is in that set, so executed_callee=True
    alone never promotes a claim.

    Rules:
      UNKNOWN     - value exactly 'UNKNOWN', no sources, confidence UNKNOWN, no registers
      HYPOTHESIS  - caller/HLE/emulator source text only, non-empty sources, confidence SOURCE_SUPPORTED
      OBSERVED    - must not contain a `registers` key (even empty/None); requires at least one EMULATOR_SOURCE reference (necessary, not sufficient);
                    callee-behavior claims (CALLEE_BEHAVIOR_FIELDS) additionally
                    require executed_callee; hardware observations
                    (HARDWARE_OBSERVATION_FIELDS, e.g. hw_bus_reset) do not;
                    any other field name is rejected;
                    and non-empty, format-valid evidence entries are required for
                    both (execution conditions, core revision, input type, verification
                    result, sha256 evidence hash). Trust level is FORMAT_ONLY: shape
                    is checked, authenticity and field correspondence are not.
      BLOCKED     - value exactly 'UNKNOWN', confidence UNKNOWN, no registers;
                    sources may cite the blocking evidence
    """
    def require(ok, message):
        if not ok:
            raise ValueError(message)

    # Input-type gate: nothing below may touch claim/source_kinds before their types
    # are known, so malformed input raises ValueError and never AttributeError/TypeError.
    require(isinstance(claim, dict), f'{context}: claim must be a dict')
    require(isinstance(source_kinds, dict), f'{context}: source_kinds must be a dict')
    require(all(isinstance(key, str) for key in source_kinds),
            f'{context}: source keys must be strings')
    require(all(isinstance(kind, str) and kind in SOURCE_KINDS for kind in source_kinds.values()),
            f'{context}: invalid source kind')
    require(isinstance(field, str), f'{context}: claim field must be a string')
    require(field in CALLEE_BEHAVIOR_FIELDS | HARDWARE_OBSERVATION_FIELDS,
            f'{context}: unknown claim field {field!r}')
    require(executed_callee is True or executed_callee is False,
            f'{context}: executed_callee must be a bool')
    require(isinstance(resolved_evidence_hashes, (set, frozenset)) and
            all(isinstance(h, str) for h in resolved_evidence_hashes),
            f'{context}: resolved_evidence_hashes must be a set of strings')
    require(all(isinstance(key, str) for key in claim), f'{context}: claim keys must be strings')
    require(isinstance(claim.get('status'), str), f'{context}: unsupported claim status (must be a string)')
    require(isinstance(claim.get('evidence', []), list), f'{context}: claim evidence must be a list')
    registers = claim.get('registers')
    require(registers is None or (isinstance(registers, list) and
                                  all(isinstance(r, str) and r in {'A', 'X', 'Y', 'S', 'P'} for r in registers) and
                                  len(registers) == len(set(registers))),
            f'{context}: registers must be a list of unique A/X/Y/S/P')
    require(set(claim) <= CLAIM_KEYS, f'{context}: unknown claim keys {sorted(set(claim) - CLAIM_KEYS)}')
    require(claim.get('status') == 'OBSERVED' or OBSERVATION_KIND_KEY not in claim,
            f'{context}: only an OBSERVED claim may declare {OBSERVATION_KIND_KEY}')
    status = claim.get('status')
    require(status in CLAIM_STATUSES,
            f'{context}: unsupported claim status {status!r}; VERIFIED is not represented')
    value = claim.get('value')
    require(isinstance(value, str) and value, f'{context}: claim value required')
    sources = claim.get('sources', [])
    require(isinstance(sources, list) and all(isinstance(x, str) for x in sources),
            f'{context}: claim sources must be a list of strings')
    require(len(sources) == len(set(sources)), f'{context}: duplicate claim sources')
    require(set(sources) <= set(source_kinds), f'{context}: claim sources must be known sources')
    confidence = claim.get('confidence')

    require(status == 'OBSERVED' or not claim.get('evidence'),
            f'{context}: only an OBSERVED claim may carry evidence')

    if status == 'UNKNOWN':
        require(value == 'UNKNOWN' and confidence == 'UNKNOWN' and sources == [] and not registers,
                f'{context}: invalid UNKNOWN claim')
    elif status == 'HYPOTHESIS':
        # HYPOTHESIS: source kind (any) never implies execution; no implicit promotion to OBSERVED.
        require(value != 'UNKNOWN' and confidence == 'SOURCE_SUPPORTED' and sources,
                f'{context}: invalid HYPOTHESIS claim')
    elif status == 'OBSERVED':
        require(value != 'UNKNOWN' and confidence == 'SOURCE_SUPPORTED' and sources,
                f'{context}: invalid OBSERVED claim')
        # Design: an OBSERVED claim has no `registers` key at all (not even [] or None).
        # Register lists belong to preserved/clobbered, which cannot be OBSERVED without
        # an executed callee.
        require('registers' not in claim, f'{context}: OBSERVED claim must not contain registers')
        require(any(source_kinds[source] == EMULATOR_SOURCE_KIND for source in sources),
                f'{context}: OBSERVED requires an EMULATOR_SOURCE source reference')
        if field in CALLEE_BEHAVIOR_FIELDS:
            require(executed_callee,
                    f'{context}: OBSERVED callee behavior requires an executed callee (verification is NOT_RUN)')
        evidence = claim.get('evidence', [])
        require(isinstance(evidence, list) and bool(evidence),
                f'{context}: OBSERVED requires non-empty evidence entries (format-checked only)')
        validate_evidence(evidence, context)
        kind = claim.get(OBSERVATION_KIND_KEY)
        require(isinstance(kind, str) and kind in OBSERVATION_KIND_FIELDS,
                f'{context}: OBSERVED requires {OBSERVATION_KIND_KEY} in {sorted(OBSERVATION_KIND_FIELDS)}')
        require(field in OBSERVATION_KIND_FIELDS[kind],
                f'{context}: observation kind {kind} does not support field {field!r}')
        # An executed callee can only back a callee-execution observation (declaration, not verified).
        require(not executed_callee or kind == 'CALLEE_EXECUTION',
                f'{context}: executed_callee=True requires observation kind CALLEE_EXECUTION, not {kind}')
        # Declaration-level fail-closed text checks (Issue #359 round 3, see the rule above _marker_fragment_refused):
        # every string under the claim (value, sources, evidence entries, ...) plus `field` must be pure ASCII and
        # carry no SYNTHETIC fragment in any single field. executed_callee=True is never a substitute.
        texts = [field, *_leaf_strings(claim)]
        require(not _marker_fragment_refused(texts),
                f'{context}: OBSERVED refuses SYNTHETIC content (not real-core provenance)')
        require(all(_is_pure_ascii(text) for text in texts),
                f'{context}: OBSERVED requires pure ASCII text (non-ASCII codepoint refused)')
        require(bool(resolved_evidence_hashes),
                f'{context}: OBSERVED requires real-core evidence, but the resolved evidence set is empty')
        for entry in evidence:
            require(_marker_text(REAL_CORE_MARKER) in _marker_text(entry['execution_conditions']),
                    f'{context}: OBSERVED evidence lacks the {REAL_CORE_MARKER} provenance marker')
            require(entry['evidence_hash'] in resolved_evidence_hashes,
                    f'{context}: OBSERVED requires real-core evidence resolved by resolve_evidence')
    elif status == 'BLOCKED':
        require(value == 'UNKNOWN' and confidence == 'UNKNOWN' and not registers,
                f'{context}: invalid BLOCKED claim')


def load_contracts(path=ROOT / 'spec/api_contracts.json'):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    validate_contracts(data, json.loads((ROOT / 'spec/api_slots.json').read_text(encoding='utf-8')),
                       (ROOT / 'docs/api-inventory.md').read_text(encoding='utf-8'))
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', nargs='?', type=Path, default=ROOT / 'spec/api_contracts.json')
    args = parser.parse_args()
    try:
        data = load_contracts(args.path)
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f'Invalid contracts: {error}\n')
    print(f"PASS: {len(data['apis'])} candidate contracts consistent; BIOS verification NOT_RUN")


if __name__ == '__main__':
    main()
