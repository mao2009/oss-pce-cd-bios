"""Pure-Python judge for Geargrafx CD-DA observations (Issue #15, diagnostic only).

Separates two different successes that look alike from the guest side:
  * READ(6) on an audio LBA: the drive returns bytes, but CdRomAudio stays IDLE and plays nothing.
  * CD-DA playback ($D8): CdRomAudio goes PLAYING and its sample BUFFER carries the BIN's PCM.
Every claim is checked against the original fixture generator (tools/make_cd_image.audio_frame),
never against the emulator's own output. These are pinned-Geargrafx facts, NOT the System Card
CD_PLAY ABI and NOT real-hardware behaviour.
"""
import json

if __package__:
    from .build_cdda import AUDIO_SECTORS, AUDIO_START_LBA
    from .make_cd_image import audio_frame
else:
    from build_cdda import AUDIO_SECTORS, AUDIO_START_LBA
    from make_cd_image import audio_frame

# CdRomAudio::CdAudioState in the pinned source (cdrom_audio.h)
PLAYING, IDLE, PAUSED, STOPPED = 0, 1, 2, 3
SAMPLES_PER_SECTOR = 588
SENTINEL = 0x5A
SECTOR_BYTES = 2048
# Guest command script (src/cdda_probe.s cdda_table): opcode and expected data-in bytes per command.
SCRIPT = [(0x08, SECTOR_BYTES), (0xD8, 0), (0xDD, 10), (0xDA, 0), (0xDD, 10), (0xD9, 0), (0xDD, 10)]
WAITS_AFTER = (1, 3, 5)   # commands followed by a cdda_wait_done checkpoint
SUBQ_SLOTS = {2: 0x80, 4: 0x90, 6: 0xA0}


def parse(stdout: str) -> dict:
    records = {"CAP": [], "HIT": [], "AUD": [], "JUMP": [], "RUN": []}
    for line in stdout.splitlines():
        tag, _, body = line.partition(" ")
        if tag in records:
            records[tag].append(json.loads(body))
    return records


def pcm(lba: int, sample: int) -> tuple:
    frame = audio_frame(lba - AUDIO_START_LBA)
    left = int.from_bytes(frame[sample * 4:sample * 4 + 2], "little", signed=True)
    right = int.from_bytes(frame[sample * 4 + 2:sample * 4 + 4], "little", signed=True)
    return left, right


def _bcd(value: int) -> int:
    return (value // 10) << 4 | value % 10


def _msf(frames: int) -> bytes:
    return bytes((_bcd(frames // 4500), _bcd(frames // 75 % 60), _bcd(frames % 75)))


def checkpoints(records: dict) -> dict:
    """{command index: (cmd_done HIT, wait_done HIT or None)} in guest order, fail-closed."""
    hits = records["HIT"]
    out, expect = {}, 0
    for i, hit in enumerate(hits):
        if hit["name"] != "cdda_cmd_done":
            continue
        if hit["x"] != expect:
            raise ValueError(f"checkpoint order: command {hit['x']} where {expect} was due")
        follow = hits[i + 1] if i + 1 < len(hits) and hits[i + 1]["name"] == "cdda_wait_done" else None
        if (follow is not None) != (expect in WAITS_AFTER):
            raise ValueError(f"command {expect}: wait checkpoint presence differs from the script")
        out[expect] = (hit, follow)
        expect += 1
    if expect != len(SCRIPT) or len(hits) != len(SCRIPT) + len(WAITS_AFTER):
        raise ValueError(f"saw {expect} of {len(SCRIPT)} command checkpoints ({len(hits)} hits)")
    return out


def check_guest(run: dict) -> None:
    if run.get("reason") != "cdda_halt_ok" or run.get("halt_confirmed") is not True:
        raise ValueError(f"guest did not stop at the success checkpoint ({run.get('reason')})")
    result = bytes.fromhex(run["result_hex"])
    if result[0] != 0:
        raise ValueError(f"guest result code {result[0]} at command {result[1]}")
    for index, (opcode, size) in enumerate(SCRIPT):
        status, message, lo, hi = result[0x10 + 4 * index:0x14 + 4 * index]
        if (status, message, lo | hi << 8) != (0, 0, size):
            raise ValueError(f"command {index} (${opcode:02X}): status/message/bytes {(status, message, lo | hi << 8)}")


def check_read6_on_audio(records: dict, run: dict, points: dict) -> dict:
    """READ(6) LBA 16 delivered bytes, yet started no CD-DA."""
    buffer = bytes.fromhex(run["buffer_hex"])
    if buffer[:SECTOR_BYTES] != audio_frame(0)[16:16 + SECTOR_BYTES]:
        raise ValueError("READ(6) on the audio LBA did not return the frame bytes 16.. of the BIN")
    if any(b != SENTINEL for b in buffer[SECTOR_BYTES:]):
        raise ValueError("READ(6) wrote beyond one sector")
    after = points[0][0]
    if after["audio"]["state"] != IDLE:
        raise ValueError(f"CD-DA state {after['audio']['state']} after READ(6); expected IDLE")
    if any(row[4] <= after["step"] for row in records["AUD"]):
        raise ValueError("CD-DA samples were produced before the play command")
    return {"bytes": SECTOR_BYTES, "cdda_state_after": "IDLE", "cdda_samples_before_play": 0}


def check_playback(records: dict, points: dict) -> dict:
    play, played = points[1]
    if play["audio"]["state"] != PLAYING or play["audio"]["start_lba"] != AUDIO_START_LBA:
        raise ValueError(f"after $D8: {play['audio']}; expected PLAYING from LBA {AUDIO_START_LBA}")
    jumps = records["JUMP"]
    if len(jumps) != 1 or jumps[0][2:4] != [AUDIO_START_LBA, 0] or not points[0][0]["step"] < jumps[0][4] <= play["step"]:
        raise ValueError(f"expected exactly one seek to LBA {AUDIO_START_LBA} during $D8, saw {jumps}")
    rows = records["AUD"]
    if len(rows) < SAMPLES_PER_SECTOR:
        raise ValueError(f"only {len(rows)} CD-DA samples; need at least one sector")
    position = AUDIO_START_LBA * SAMPLES_PER_SECTOR
    for lba, sample, left, right, _ in rows:
        if lba * SAMPLES_PER_SECTOR + sample != position:
            raise ValueError(f"CD-DA sample stream not contiguous at LBA {lba} sample {sample}")
        if not AUDIO_START_LBA <= lba < AUDIO_START_LBA + AUDIO_SECTORS:
            raise ValueError(f"CD-DA played LBA {lba} outside the audio track")
        if (left, right) != pcm(lba, sample):
            raise ValueError(f"CD-DA sample at LBA {lba}+{sample} is {(left, right)}, BIN has {pcm(lba, sample)}")
        position += 1
    end_lba, end_sample = divmod(position, SAMPLES_PER_SECTOR)
    if played["audio"]["state"] != PLAYING or played["audio"]["current_lba"] <= AUDIO_START_LBA:
        raise ValueError("CD-DA did not advance while the guest waited")
    pause = points[3][0]
    if rows[-1][4] > pause["step"]:
        raise ValueError("CD-DA samples continued after $DA pause completed")
    if (pause["audio"]["current_lba"], pause["audio"]["current_sample"]) != (end_lba, end_sample):
        raise ValueError("pause position differs from the last played sample")
    return {"samples_verified_against_bin": len(rows), "first": rows[0][:2], "last": rows[-1][:2],
            "sectors_played": round(len(rows) / SAMPLES_PER_SECTOR, 2)}


def check_pause_stop(points: dict) -> dict:
    pause, paused = points[3]
    held = (pause["audio"]["current_lba"], pause["audio"]["current_sample"])
    if pause["audio"]["state"] != PAUSED or paused["audio"]["state"] != PAUSED:
        raise ValueError("state after $DA is not PAUSED")
    if (paused["audio"]["current_lba"], paused["audio"]["current_sample"]) != held:
        raise ValueError("position moved while paused")
    stop, stopped = points[5]
    for hit in (stop, stopped, points[6][0]):
        if hit["audio"]["state"] != STOPPED or (hit["audio"]["current_lba"], hit["audio"]["current_sample"]) != held:
            raise ValueError("state after $D9 mode 0 is not STOPPED at the held position")
    return {"paused_at": list(held), "stopped_at": list(held)}


def check_subq(run: dict, points: dict) -> dict:
    """$DD reply: byte0 = state, then adr/control, track, index, relative MSF, absolute MSF (BCD)."""
    result = bytes.fromhex(run["result_hex"])
    seen = {}
    for index, offset in SUBQ_SLOTS.items():
        reply = result[offset:offset + 10]
        low = points[index - 1][1] or points[index - 1][0]
        high = points[index][0]
        state = {2: PLAYING, 4: PAUSED, 6: STOPPED}[index]
        lbas = range(low["audio"]["current_lba"], high["audio"]["current_lba"] + 1)
        matched = [lba for lba in lbas if reply == bytes((state, 0x01, 0x02, 0x01))
                   + _msf(lba - AUDIO_START_LBA) + _msf(lba + 150)]
        if not matched:
            raise ValueError(f"$DD reply {reply.hex()} after command {index - 1} does not match state {state} "
                             f"and LBA {lbas.start}..{lbas.stop - 1}")
        seen[index] = {"reply_hex": reply.hex(), "state": state, "lba": matched[0]}
    return seen


def check_audio_accounting(run: dict) -> None:
    if run["unattributed_nonzero"] != 0:
        raise ValueError(f"{run['unattributed_nonzero']} nonzero CD-DA samples not explained by play-position advance")


def check_all(records: dict) -> dict:
    if len(records["CAP"]) != 1 or not all(records["CAP"][0].get(k) for k in ("instruction_step", "cdrom_hardware", "cdrom_media")):
        raise ValueError("real instruction stepping / System Card CD path capability missing")
    if len(records["RUN"]) != 1:
        raise ValueError("probe did not finish exactly one run")
    run = records["RUN"][0]
    check_guest(run)
    points = checkpoints(records)
    check_audio_accounting(run)
    return {"read6_on_audio_lba": check_read6_on_audio(records, run, points),
            "cdda_play": check_playback(records, points),
            "pause_stop": check_pause_stop(points),
            "subq": check_subq(run, points)}
