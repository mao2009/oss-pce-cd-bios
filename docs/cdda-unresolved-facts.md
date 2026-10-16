# CD-DA 状態観測 (#15) の未解決事項

対象: CD_PLAY (`$06`, `$E012`), CD_SEARCH (`$07`), CD_PAUSE (`$08`) の実装前提。
状態: spec=HYPOTHESIS / implementation=NOT_STARTED / verification=NOT_RUN。

## 現在の到達点

- ホスト検証 (VERIFIED, host only): `tests/host/test_cdimage_mixed.py` (#113) で混合モードCUE/BINの
  トラック境界・データ/音声の区別を確認。`tests/host/test_cdda_state.py` で、トラック境界に基づく
  状態遷移モデル (STOPPED / PLAYING / PAUSED / ENDED) を検証。これは我々のモデルであり、
  実機コアや System Card の挙動の検証ではない。
- 実コア検証 (NOT RUN): Geargrafx 上の CD-DA 再生観測は実施していない。
- 観測点の特定 (ソース調査, NOT RUN): Geargrafx 固定版 `b49ae82` (`tools/dependencies.json`,
  SHA-256 検証済み) のソースで、CD-DA の状態と位置を読む経路を確認した。
  - 状態・位置: `GeargrafxCore::GetCDROMAudio()` (`src/geargrafx_core.h:111`) から
    `CdRomAudio::GetState()` (`src/cdrom_audio.h`) で `CURRENT_STATE` (PLAYING=0x00 / IDLE=0x01 /
    PAUSED=0x02 / STOPPED=0x03)、`CURRENT_LBA`、`START_LBA`、`STOP_LBA` を読める。
  - SCSI 側: 再生開始 `0xD8` (`src/scsi_controller.cpp:457,564`)、停止位置 `0xD9`、一時停止 `0xDA`、
    サブチャネル Q 読み出し `0xDD` (現在位置、`scsi_controller.cpp:646-700`)。
- 実コア観測基盤: #135 (`feat/i-cdda-core-probe`, 未マージ) で SCSI `0xD8`/`0xD9` 経由の観測基盤を
  追加予定。main 取り込み後、#135 のマージで本節を更新する。
- 本 PR では実コア試験を実装していない。観測点はソース上で確認したのみで、コアを実行して値を読んだ
  証跡はない (NOT RUN)。
- ABI: System Card の CD_PLAY/CD_PAUSE/CD_SEARCH の引数・戻り値は UNKNOWN のまま。

## ホストモデルの範囲と差異 (`tests/host/test_cdda_state.py`)

- `ENDED` はホストモデル内部の状態であり、コアの `CdRomAudio` 状態値として主張しない。
- `stop()` はモデルの位置を消去する。一方、SCSI `0xD9` mode 0 は #135 の観測で位置を保持する。
  この差異は残す。理由: モデルは未確定の CD_PLAY/CD_STOP ABI を表すもので、SCSI レベルの観測とは
  層が異なる。SCSI 観測に合わせると、未確認の ABI 対応を推測で固定することになる。
  #135 のマージ後も差異として残し、ABI が確定した時点で見直す。
- 終端処理は、盤の最終トラックの場合だけモデル化する。途中の音声トラックの終端 (停止、ループ、
  次トラックへの移行) は UNKNOWN のため、`tick()` は到達を拒否する (`NotImplementedError`)。

## 未解決事項 (CD_PLAY 実装の前提)

1. 引数: トラック番号か、開始/終了の LBA か、MSF か。どの組み合わせが有効か。
2. 引数の受け渡し: A/X/Y のどれか、ワーク RAM のどのバイト (`$20F8-$20FF` 等) か、スタックか。
3. 戻り値: A/X/Y/P (キャリー等) のどれが結果を示すか。成功/失敗のコードは何か。
4. データトラックを指定した場合の結果 (拒否か、無視か、データとして再生か)。
5. 存在しないトラック番号、または範囲外の LBA の結果。
6. 終了位置の扱い: トラック末尾で自動停止するか、次トラックへ進むか、無音で続くか。
7. CD_PAUSE と再開の関係: 再開は CD_PLAY か CD_PAUSE か。一時停止中の位置は保持されるか。
8. CD_STOP 相当の操作の有無、停止後の位置がリセットされるか。
9. CD_SEARCH の挙動: 再生中/停止中/一時停止中それぞれで、位置変更後の状態は何か。
10. ドライブ busy/timeout: 完了待ちの方法、タイムアウト時の戻り値、割り込み (IRQ) の関与。
11. 再生位置の取得: Q サブチャネル等で現在位置を取れるか。取れる場合の API 名と形式。
12. MPR/バンク条件と、呼び出し時の割り込み状態 (CLI/SEI) の前提。
13. エミュレータ観測点: 状態・位置は `CdRomAudio::GetState()` で読める (上記ソース調査)。未確認は
    ゲストから再生を開始する経路 (SCSI `0xD8` のバス発行) と、`geargrafx_cdread_probe.cpp` が取得する
    音声バッファが CD-DA 再生の証拠になるか。
14. System Card 3.0 での上記の正式な記述。公開されている旧版ソースとの差異 (docs/api-inventory.md の
    既知の矛盾台帳を参照) は未解決。

## 次の作業の前提

- 上記 1-3 と 6-7 が確定するまで CD_PLAY/CD_PAUSE/CD_SEARCH は実装しない。
- 観測点は確認済み。#135 のマージ後に、実コアで状態遷移を読めるまで Geargrafx 診断ROM は BLOCKED のまま。
- 実コアでの PASS/FAIL を主張するには、フィクスチャ・ROM・エミュレータのハッシュを記録した
  実行結果が必要。本 PR はそれを含まない。
