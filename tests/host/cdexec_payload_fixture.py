"""Issue #240: 合成 CD_EXEC ロードペイロード fixture（開発専用・原作・ROM/媒体不要）。

BIOS でも CD_EXEC 実装でもなく、System Card や商用データに由来しない。
命令列は HuC6280（65C02 互換部分集合）を手で符号化した自作コード。

合成セクタ（2048 バイト）のレイアウト:
  0..3  magic b"CDLP"
  4..5  ペイロード長（リトルエンディアン、1..MAX_PAYLOAD）
  6..7  ペイロード内の entry オフセット（リトルエンディアン、< 長さ）
  8..   ペイロード本体、以降は 0 埋めで 2048 バイトへ

配置契約: ペイロードは PLACE_BASE ($3000) へ置かれ、expected entry は EXPECTED_ENTRY ($3000)。
ペイロードは [PLACE_BASE, MARKER_ADDR) に収まり、RTS で戻る（呼び出し側は JSR）。
マーカー: 実行後 $3F00 = $A5, $3F01 = $5A。

負例の reason: NO_MEDIA / BAD_SECTOR_SIZE / BAD_MAGIC / BAD_LENGTH / BAD_ENTRY / BAD_DEST / BAD_SHA256。
"""
import hashlib

SECTOR_BYTES = 2048
MAGIC = b"CDLP"
HDR_BYTES = 8
MAX_PAYLOAD = SECTOR_BYTES - HDR_BYTES
PLACE_BASE = 0x3000
MARKER_ADDR = 0x3F00
MARKER_BYTES = b"\xa5\x5a"
EXPECTED_ENTRY = 0x3000

# (符号化バイト, ニーモニック)。自作の 11 バイト。
LISTING = (
    (b"\xa9\xa5", "LDA #$A5"),
    (b"\x8d\x00\x3f", "STA $3F00"),
    (b"\xa9\x5a", "LDA #$5A"),
    (b"\x8d\x01\x3f", "STA $3F01"),
    (b"\x60", "RTS"),
)
PAYLOAD_CODE = b"".join(code for code, _ in LISTING)
# 以下の SHA-256 と EXPECTED_ENTRY は合成 fixture の設計値（純正 BIOS・純正 callee 由来ではない）。
EXPECTED_CODE_SHA256 = "7bd3e70e73d178b492c8b3d216b53ab1faaa385cdfa4fc6c1301b0cdaa1392f6"
EXPECTED_SECTOR_SHA256 = "530822389a169917a6ef775b5ddd972556eaf684558436e5b3b143fa5c8e9be2"

# 安全な非実行参照ケース（純正参照入力を模すが、命令・アドレスは持たない）。未確定値は None（UNKNOWN）。
# 純正 slot 05 の JSR 実行は 0 回。危険な純正 JSR は #173 の実行時遮断が証明されるまで許可しない。
REFERENCE_JSR_ALLOWED = 0
REFERENCE_CASE = {"entry": None, "mpr": None, "stack": None}


class PayloadError(ValueError):
    def __init__(self, reason, detail=""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class LoadPlan:
    def __init__(self, dest, entry, code):
        self.dest = dest
        self.entry = entry
        self.code = code


def build_sector(code, entry_offset=0):
    """合成セクタを作る。範囲外の長さ・entry は ValueError（負例の生成元にはテストで raw 改変を使う）。"""
    if not 1 <= len(code) <= MAX_PAYLOAD:
        raise ValueError("payload length out of range")
    if not 0 <= entry_offset < len(code):
        raise ValueError("entry offset out of range")
    header = MAGIC + len(code).to_bytes(2, "little") + entry_offset.to_bytes(2, "little")
    return (header + bytes(code)).ljust(SECTOR_BYTES, b"\x00")


def plan_load(sector, dest=PLACE_BASE):
    """構造検査のみ（SHA は見ない）。媒体・サイズ・magic・長さ・entry・宛先を検査する。"""
    if sector is None:
        raise PayloadError("NO_MEDIA")
    if len(sector) != SECTOR_BYTES:
        raise PayloadError("BAD_SECTOR_SIZE", str(len(sector)))
    if bytes(sector[:4]) != MAGIC:
        raise PayloadError("BAD_MAGIC")
    length = int.from_bytes(sector[4:6], "little")
    entry_off = int.from_bytes(sector[6:8], "little")
    if not 1 <= length <= MAX_PAYLOAD:
        raise PayloadError("BAD_LENGTH", str(length))
    if entry_off >= length:
        raise PayloadError("BAD_ENTRY", str(entry_off))
    if dest + entry_off != EXPECTED_ENTRY:
        raise PayloadError("BAD_DEST", f"entry {dest + entry_off:#06x} != {EXPECTED_ENTRY:#06x}")
    # 防御的検査: 現在の entry 不変条件では dest > PLACE_BASE かつ末尾超過は到達しない
    if dest < PLACE_BASE or dest + length > MARKER_ADDR:
        raise PayloadError("BAD_DEST", f"[{dest:#06x}, {dest + length:#06x}) outside region")
    code = bytes(sector[HDR_BYTES:HDR_BYTES + length])
    return LoadPlan(dest=dest, entry=dest + entry_off, code=code)


def verify_sector_sha256(sector):
    if hashlib.sha256(bytes(sector)).hexdigest() != EXPECTED_SECTOR_SHA256:
        raise PayloadError("BAD_SHA256")


def load_payload(sector, dest=PLACE_BASE):
    """#242 の入口。構造検査の後にセクタ SHA-256 を照合し、成功時に LoadPlan を返す。"""
    plan = plan_load(sector, dest)
    verify_sector_sha256(sector)
    return plan


def marker_present(marker_bytes):
    """$3F00..$3F01 の 2 バイトを受け取り、マーカーと一致するかを返す。"""
    return bytes(marker_bytes) == MARKER_BYTES


def decide_reference_jsr(entry=None, mpr=None, stack=None, authorized=False, monitored=False):
    """純正参照入力の JSR 判定。実行は一切せず、常に BLOCKED を返す（許可数は REFERENCE_JSR_ALLOWED = 0）。

    未確定の entry/MPR/stack、未承認、監視不足、および #173 の遮断未証明を理由として列挙する。
    方針スタブであり、許可領域外（RAM・I/O・MPR）の検査は行わない（ゲートではない）。
    """
    reasons = [name for name, value in (("UNKNOWN_ENTRY", entry), ("UNKNOWN_MPR", mpr), ("UNKNOWN_STACK", stack))
               if value is None]
    if not authorized:
        reasons.append("NOT_AUTHORIZED")
    if not monitored:
        reasons.append("NOT_MONITORED")
    reasons.append("GUARD_173_UNPROVEN")
    return "BLOCKED", reasons
