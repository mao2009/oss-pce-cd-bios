# CD-DA 実コア観測診断 (#15)

状態: 診断のみ。System Card の CD_PLAY (`$06`) / CD_PAUSE (`$08`) / CD_SEARCH (`$07`) ABI は UNKNOWN。
本診断は Geargrafx 固定版 (`tools/dependencies.json` の `b49ae82`) の SCSI 層の挙動だけを観測する。

- 実行: `make dev-cdda-integration` (ROM: `src/cdda_probe.s`、判定: `tools/cdda_verify.py`、
  フロントエンド: `tests/integration/geargrafx_cdda_probe.cpp`)
- ディスク: `tools/make_cd_image.py` で生成する自作の混合モード CUE/BIN。データ LBA 0-15 (MODE1/2352)、
  音声 LBA 16-79 (トラック 02)。音声の右チャンネルは左の `~L` (`distinct_right=True`) で、L と R は常に異なる。

## 観測点

| 対象 | 観測点 (固定版ソース) |
|---|---|
| 状態・位置 | `GeargrafxCore::GetCDROMAudio()->GetState()`: `CURRENT_STATE`, `START_LBA`, `STOP_LBA`, `CURRENT_LBA`, `CURRENT_SAMPLE`, `STOP_EVENT`, `SEEK_CYCLES`, `PLAYBACK_DELAY_CYCLES` |
| 音声バッファ | `CdRomAudio_State::BUFFER[0..FRAME_SAMPLES)` (ミックス前の CD-DA サンプル) |
| SCSI | `ScsiController::ExecuteCommand`: `$D8` 開始 / `$D9` 停止 / `$DA` 一時停止 / `$DD` サブQ |

## 判定の根拠と範囲

- 「再生成功」とみなす根拠は次の 2 点だけ。
  1. `$D8` 後に `CURRENT_STATE` が PLAYING になること。
  2. 音声バッファのサンプル列が LBA 16 から途切れずに進み、各サンプルが期待値と一致すること。
- TOC 上のトラック種別 (READ TOC `$DE` の音声/データ判定) は判定に使っていない。
  データトラックに `$D8` を発行すると Geargrafx は PLAYING に入り無音を出す (負例で観測)。
  そのため PLAYING 状態だけでは再生成功とは言えない。この場合は PCM の不一致で拒否される。
- 「READ(6) 成功」は別に判定する。READ(6) で音声 LBA を読むとバイト列は返るが、CdRomAudio は IDLE のままで
  サンプルは出ない。

## 期待値の出どころ

期待サンプルは `make_cd_image.audio_frame(distinct_right=True)` で計算する。これは BIN を書き出したのと
**同じ関数** であり、BIN から独立した値ではない。一致によって分かるのは、エミュレータが BIN の PCM を
正しい LBA・サンプル位置・バイト順・チャンネルで出したことまで。生成関数自体の誤りは検出できない。

## 負例 (実コア、同じ判定で拒否)

| 名前 | 内容 | 拒否理由 (照合文字列) |
|---|---|---|
| `tampered_pcm_byte_in_lba20` | LBA 20 の byte 100 (サンプル 25 左の下位バイト) を `^0x40` | `CD-DA sample at LBA 20+25 is (…), BIN has (…)` を値まで固定 |
| `channels_swapped_lba21_sample30` | LBA 21 サンプル 30 の L/R を入れ替え | `CD-DA sample at LBA 21+30 is (R, L), BIN has (L, R)` |
| `play_on_data_track` | データのみのディスクに `$D8` | PCM 不一致 (PLAYING かつ無音) |
| `d8_byte1_zero_seeks_paused` | `$D8` byte1=0 | PLAYING にならない (PAUSED、サンプルなし) |

## サンプル数のぶれ

1 回の実行で検証されるサンプル数は約 29,685〜29,686 で、実行ごとに 1 程度ぶれる。判定はサンプル数を
固定せず、1 セクタ (588) 以上であることと連続性・値の一致だけを見る。

原因は **推測 (未検証)**: Geargrafx は起動時に一部の状態 (SCSI の読み出し位相など) を乱数で初期化する。
そのため `$D8` 完了までの命令数が実行ごとに変わり、ゲストの固定長の待ちループと再生開始の位相がずれる、
と考えている。乱数のシードを固定した比較は行っていない。

## 未確認

- System Card の CD_PLAY ABI (引数・戻り値・エラー): UNKNOWN。
- `$D9` mode 1-3 (ループ / IRQ / 指定位置停止) と、トラック末尾での挙動: NOT RUN。
- 実機・第 2 エミュレータ: NOT RUN。
