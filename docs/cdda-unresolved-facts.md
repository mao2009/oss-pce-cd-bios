# CD-DA 状態観測 (#15) の未解決事項

対象: CD_PLAY (`$06`, `$E012`), CD_SEARCH (`$07`), CD_PAUSE (`$08`) の実装前提。
状態: spec=HYPOTHESIS / implementation=NOT_STARTED / verification=NOT_RUN。

## 現在の到達点

- ホスト検証 (VERIFIED, host only): `tests/host/test_cdimage_mixed.py` (#113) で混合モードCUE/BINの
  トラック境界・データ/音声の区別を確認。`tests/host/test_cdda_state.py` で、トラック境界に基づく
  状態遷移モデル (STOPPED / PLAYING / PAUSED / ENDED) を検証。これは我々のモデルであり、
  実機コアや System Card の挙動の検証ではない。
- 実コア検証 (NOT RUN): Geargrafx 上の CD-DA 再生観測は実施していない。
- BLOCKED: Geargrafx 診断ROMは作成していない。main の CD 基盤 (`src/cd_read_probe.s`,
  `tests/integration/geargrafx_cdread.py`) は `$1800-$1804` の SCSI バス経由の READ(6) データ経路のみを
  露出している。CD-DA の再生中/一時停止/終了を示す観測点 (レジスタまたは RAM 位置) を読む経路が
  main に存在しない。`geargrafx_cdread.py` が観測するのは音声 LBA に対する READ(6) の結果だけ。
  BLOCKED の具体的な欠落観測点: CD-DA トランスポート状態 (再生中/停止/一時停止/終了) と
  現在位置 (LBA または MSF) を、コアのどこから読むかが未確定。

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
13. エミュレータ観測点: Geargrafx で再生状態を読む手段 (レジスタ、ワーク RAM、音声出力バッファ) の
    どれが再生状態を示すか。`geargrafx_cdread_probe.cpp` が取得する音声バッファは CD-DA 再生の証拠に
    なるかが未確認。
14. System Card 3.0 での上記の正式な記述。公開されている旧版ソースとの差異 (docs/api-inventory.md の
    既知の矛盾台帳を参照) は未解決。

## 次の作業の前提

- 上記 1-3 と 6-7 が確定するまで CD_PLAY/CD_PAUSE/CD_SEARCH は実装しない。
- 観測点 (項目 13) が確定し、main の経路で読めるまで Geargrafx 診断ROMは BLOCKED のまま。
- 実コアでの PASS/FAIL を主張するには、フィクスチャ・ROM・エミュレータのハッシュを記録した
  実行結果が必要。本 PR はそれを含まない。
