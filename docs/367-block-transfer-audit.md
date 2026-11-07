# Issue #367: ブロック転送（TII/TIN/TDD/TIA/TAI）とデバイス起点書込みの事前遮断監査

## 目的と状態

親 #173（V04: ブロック転送、V07: デバイス起点書込み）の監査。合成 ROM・合成入力のみを対象とする。
**状態: BLOCKED / release_eligible=false。** slot `$01` と全スロットのゲートは開けない。

## 結論（要約）

- 5 命令はいずれも **1 命令内で length 回のバイト書込みを行う**（複数書込み）。静的な範囲検査では、書込み先が許可域外に届くかを命令実行前に判定できる（`tools/block_xfer_audit.py`）。
- **実コアでの書込み前遮断は実測対象になった（旧記述の NOT_RUN は置き換え）。** `tests/integration/geargrafx_block_write_guard.py`（`make block-write-guard`、`make integration` に含まれる）が pinned Geargrafx（b49ae82）と検証専用パッチ付きの実コアで、TII/TIN/TIA/TDD/TAI の 1 バイト転送を合成メディアで実行する。**この#612時点の suite は30ケース**（各命令6種: 拒否・フックなし対照・許可の3種に、#459のスタックpush用の3種を加えたもの）。後続#613はsafeabort拒否/許可を加え、suiteを40ケースへ拡張する。30件は#612時点の記録であり、後続suiteの現行件数ではない。
- **最新の拡張suite:** 後続 #613 は safeabort 拒否/許可の2 variantを各命令に追加し、合計40ケースへ拡張する。この数は #613 のsuiteに対するもので、以下の #612 当時の30件記録とは別である。
- **per-byte 拒否の継続挙動は 5 命令で実測済み（本 PR）。** `tests/integration/geargrafx_block_midrefusal.py`（`make block-mid-refusal`、30 ケース）が、境界跨ぎの複数バイト転送で、拒否バイトの前後の許可バイトが着地し、転送が継続して TransferEnd で A/X/Y が復元されることを 5 命令で測定した。hook が拒否しても転送状態と TransferEnd は進むため、安全停止の証拠ではない。命令全体の安全な中断は、全体事前拒否（preflight、matrix suite 51 ケースと #473 stack-s6）として実測済みであり、per-byte 方式では成立しない。
- 試験の拒否方針（書込み先が $FF ページの場合に拒否）は probe の中に書かれており、本番ゲートの証拠ではない。driver は「コアのフック点が書込み前に拒否できる」ことだけを示す。
- **#459（TransferStart の A/X/Y push）:** 旧状態では、スタックページ（MPR1）を $FF にマップすると A/X/Y の push が VDC に着地した（実測: AR 0→7、R[7] 0→564）。検証専用パッチに TransferStart 用の書込み前フックを追加し、スタックページが $FF のとき push だけを拒否する。拒否した場合、push は書かれず S は 3 だけ動く（TransferEnd の pop と釣り合う）。**適用範囲は TransferStart の push のみ。** PHA/PHP/JSR/IRQ などの他の push、TransferEnd の pop（$FF ページからの読出し。ガード対象外）、その他の書込みは対象外。拒否方針は probe 内に書かれたテスト方針で、本番ゲートの証拠ではない。
- **#459 の拒否経路は「安全に停止する経路」ではない（実コアで実測）。** TransferEnd は常に 3 バイトを pop する。push を拒否するとその 3 スロットは一度も書かれていないため、スタックページが $FF のときは $2102/$2103/$2104（VDC のデータ読み出しバッファ LSB/MSB とステータスレジスタ）を**読み出す**ことになる。実測値は下記の「#459 拒否後の実コア追跡」節。A/X/Y は読み出した値で上書きされ、$2104 の読み出しは VDC ステータスレジスタを**クリア**する（IRQ1 解除）。ブロック転送本体も実行される（転送先バイトは着地する）。したがって push の拒否だけでは安全性を証明できず、**本番用安全機構が必要**（スタックページを $FF にマップしない、または TransferEnd の pop まで覆うゲート）。**検証専用パッチは本番用安全機構ではない。** ゲートは BLOCKED のまま。
- 静的検査の結果は PASS ではない。`gate` は常に BLOCKED。
- デバイス起点 RAM 書込み（W17）はソース列挙済み（#419、本 PR で解決済み。`docs/research/367-device-write-audit.md`、`tests/fixtures/367_device_write_sites.tsv`、25 行）。検索範囲では CPU 以外が実行時に WRAM へ書く経路は見つからなかった。旧 UNKNOWN 行（MB128、Arcade Card）は CPU 起点に解決した。実コアでのデバイス起点書込みの実測はない（UNMEASURED）。

## 実装したもの（スタンドアロン）

| ファイル | 内容 |
|----------|------|
| `tools/block_xfer_audit.py` | 5 命令の書込み先集合（過大近似）、MPR から許可述語を作る関数、静的判定。出力は REFUSED_STATIC / PERMITTED_STATIC_ONLY と gate=BLOCKED のみ |
| `tests/host/test_block_xfer_audit.py` | 正例・負例（窓外、ハードウェアページ、ROM バンク、TDD 下方向、16bit ラップ）・変異例（ハードウェア判定の欠落、窓の欠落、TDD 方向）。19 件 |
| `docs/367-block-transfer-audit.md` | 本書 |

既存の実コアプローブ（`tests/integration/`）、`spec/`、共有フック（#365）、DEV ROM ジャンプテーブルには一切触れていない。どのルーチンも DEV ROM に配線していない（配線数 0）。DEV_CALLABLE も主張しない。

## ブロック転送の書込み先モデル

### pinned コアの実装監査（b49ae82a）

`src/huc6280_opcodes_inline.h` の実装を読み取り専用で再確認した。opcode は
TII=`$73`、TIN=`$D3`、TDD=`$C3`、TIA=`$E3`、TAI=`$F3` で、各 operand は
`source:u16, destination:u16, length:u16`（TransferStart の Fetch16 x3）である。
TransferStart は A/X/Y を stack に push し、各後続 `RunInstruction` は
`m_transfer_state==2` のとき一 byte だけ `Memory::Write(dest, Memory::Read(src,true),true)`
を行い、PC を 1 byte decrement して同じ block opcode を再実行する。完了時だけ
TransferEnd が stack pop と PC+6 を行う。TII の別実験は命令内 byte state 間の境界を観測したが、
この source 読みと block-write driver は5命令すべての同等な single-step 実測を意味しない。

| 命令 | opcode | source/destination 更新 |
|---|---:|---|
| TII | `$73` | source++, destination++ |
| TIN | `$D3` | source++, destination fixed |
| TDD | `$C3` | source--, destination-- |
| TIA | `$E3` | source++, destination += `count` の偶奇で +1/-1 |
| TAI | `$F3` | source += `count` の偶奇で +1/-1, destination++ |

`Memory::Write` が MPR の CPU page mapping を解決する通常の byte-write path である。
しかし検証専用 patch の hook 以外に #173 の production gate へ接続された per-byte
allowlist はない。patch の hook は `Memory::Read` と `Memory::Write` の前にあるため、
この限定 probe では副作用前である。一方、hook が拒否しても transfer state/count と
TransferEnd は進むため、これは安全な命令停止ではない。

### 2026-10-31 再実行結果と境界

`make block-write-guard` を#612時点のsuiteで再実行した。5 opcode x (hardware-page refusal / no-hook RED /
WRAM positive / stack-$FF refusal / stack-$FF no-hook RED / WRAM-stack positive) の30 synthetic
real-core cases は PASS した。この実行が実証するのは **MPR page=$FF の一 byte destination
store に限る verification-only pre-write hook** である。ROM bank、非-$FF forbidden RAM、
MPR alias、multi-byte boundary crossing、length=0、TIA/TAI の複数 byte 順序、命令開始前の
全体拒否は、この run では実証していないため BLOCKED である。

後続 #613 のsafeabort extensionを含む40-case suiteも別途実行され、5命令のallow/deny
callback経路を含めてPASSした。この拡張結果は#612時点の30-case runの結果と区別する。

別の `safeabort` mode は TransferStart の直前に検証専用 hook を置き、拒否時は operand
6 byte を skip して transfer state に入らない。この mode では5命令とも stack push/pop、
destination write、VDC status clear、A/X/Y破壊を観測せず終了した。これは
**pinned core に恒久実装された API ではなく** test patch の最小提案であり、production gate や
ABI の安全性を解放しない。

| 命令 | 書込み先の動き | モデル | 実測状況 |
|------|----------------|--------|------------|
| TII | dst を +1（src も +1） | 厳密（16bit ラップ） | 1-byte は実コア確認済み。複数byte（length 1/2/3/4/8、境界跨ぎ）は matrix / midrefusal suite で実測済み。16bit ラップの全体（65536 バイト）は未実測（下記 length 0 の節を参照） |
| TDD | dst を -1（src も -1） | 厳密（16bit ラップ） | 同上。逆方向の境界跨ぎ（`$4000` からの逆行）も実測済み |
| TIN | dst 固定（src は +1） | 厳密 | 同上。固定宛先の複数回拒否も実測済み |
| TIA | dst が交互（src は +1） | dst..dst+length の過大近似 | 交互順序は midrefusal suite で実測済み（`$3FFF`/`$4000` の交互書込み）。静的モデルの過大近似は保守的であり、実測で置き換えた範囲は #473 への申送りの対象である |
| TAI | src が交互（dst は +1） | dst の集合は TII と同じ | 交互順序は midrefusal suite で実測済み（src `$1E80`/`$1E81` の交互読み） |

- length 0 は最悪ケース（0x10000 バイト）として扱う。長さ 0 の規則は、preflight による拒否として実測済みである（matrix suite の `tii-len0`。u16 ラップで 65536 バイトになることは pinned ソースの型定義から確認できるが、65536 バイト全体の実行は未実測）。
- 過大近似のため、拒否は保守的（過剰拒否はありうる）。許可が楽観的になることはない。

## #459 拒否後の実コア追跡（TransferEnd）

push 拒否後に何が起きるかを実コア（pinned Geargrafx b49ae82、合成 BIOS）で追跡した。probe は実行直前に
VDC のデータ読み出しバッファとステータスレジスタへ既知の値（`0xBEEF` / `0x05`）を書き込み、pop がどの
レジスタを読んだかを特定できるようにしている。`0x05` はリセット状態で VDC が自発的に立てないビットのみで
構成され（イベント起因ビットは CR の制御ビットでゲートされる）、読み出しで副作用が観測できる。

| 観測項目 | `stackff-armed`（push 拒否） | `stackff-unguarded`（対照） | `stackwram-armed`（対照: WRAM スタック） |
|----------|------------------------------|-----------------------------|---------------------------------------|
| VDC AR | 0 → 0（**書き込み副作用なし**） | 0 → 7 | 0 → 0 |
| VDC R[7] | 0 → 0（**書き込み副作用なし**） | 0 → 564 | 0 → 0 |
| VDC ステータスレジスタ | `0x05` → `0x00`（**読み出しでクリア**） | `0x05` → `0x00` | `0x05` → `0x05`（読まれない） |
| 最終 A | `$BE`（読み出した MSB。元の `$12` を失う） | `$BE` | `$12`（スタックから復元） |
| 最終 X | `$EF`（読み出した LSB。元の `$34` を失う） | `$EF` | `$34`（スタックから復元） |
| 最終 Y | `$05`（ステータス値。元の `$07` を失う） | `$05` | `$07`（スタックから復元） |
| S | `$04` → `$04`（釣り合う） | `$04` → `$04` | `$04` → `$04` |
| P | `$04` → `$04`（不変） | `$04` → `$04` | `$04` → `$04` |
| PC | 終了点（`BRA`）へ到達 | 同左 | 同左 |
| MPR 8 本 | 変更なし | 変更なし | 変更なし |
| 転送先バイト | `$5A` が着地（**転送本体は実行される**） | `$5A` | `$5A` |

読み出したのは「一度も書かれていないスタックスロット」であり、スタックではない。`stackwram` 対照では
A/X/Y がスタックから復元され、VDC ステータスレジスタも読まれない（`0x05` のまま）ため、`stackff` の
A/X/Y 上書きとステータスレジスタのクリアは **TransferEnd の pop が $FF ページの I/O レジスタを読んだ結果**
であり、guard の副作用ではないことが対照実験で示される。

したがって、push の拒否は **書き込み副作用だけを止めている**。拒否後も (1) I/O 領域の読み出しが生じる、
(2) A/X/Y が破壊される、(3) VDC ステータスレジスタのクリアという I/O 副作用が生じる、(4) 命令自体は最後まで
実行される、の 4 点が実測された。**拒否後に安全に処理を停止する手段は push ガードには存在しない。**
本番用安全機構（スタックページの $FF マップを禁止する、または TransferEnd の pop まで覆うゲート）が必要です。
検証専用パッチは本番用安全機構の代替にはならないため、**#459 のゲートは BLOCKED のまま**とする。

### #459 の観測範囲と未検証の範囲（明示）

**観測できた範囲（実コア実測）**

- 対象: pinned Geargrafx b49ae82 の TransferStart（TII/TIN/TIA/TDD/TAI 共通フック）。1 バイト転送。
  合成 BIOS（`BIOS: SYNTHETIC`）と合成メディアのみ。純正 BIOS は対象外。
- スタックページが $FF の場合の push 拒否: 拒否回数 1、VDC AR 不変、R[7] 不変、WRAM のスタック画像も
  書き変わらない（push が書かれなかったことの直接証拠）。
- TransferEnd の pop 経路: push 拒否後も TransferEnd は 3 スロットを pop する。その 3 スロットは一度も
  書かれていないため、$FF ページ（`$2100|S`）の I/O レジスタ `$2102`（データ読み出しバッファ LSB）、
  `$2103`（MSB）、`$2104`（ステータスレジスタ）を**読み出す**。probe が事前に `$2102/$2103` に `0xBEEF`、
  `$2104` に `0x05` を置いているため、pop が読んだ値を A/X/Y から逆算でき、
  「pop が読んだのはスタックではなく I/O レジスタ」であることが対照実験で示される。
- 記録した最終状態: A、X、Y、S、P、PC、MPR 8 本、VDC AR / R[7] / ステータスレジスタ / データ読み出しバッファ、
  転送先バイト、guard の呼び出し・拒否回数・観測 SP。
- 対照実験 2 種: `stackff-unguarded`（フック無し。push が VDC に着地する）、
  `stackwram-armed`（スタックが WRAM。A/X/Y がスタックから復元され、VDC ステータスレジスタは読まれない）。

**未検証の範囲（#459 当時の PR では測っていない。以下の注記で後続の実測に置き換える）**

注: この節は #459 追跡時点（#612/#613）の範囲記録である。後続の matrix suite（51 ケース）、midrefusal suite（30 ケース）、および `docs/research/367-device-write-audit.md` の更新で、以下は実測済みまたはソース解決済みである。残る未検証は、デバイス起点書込みの実コアプローブ、オペランドページ非 WRAM の fail-closed 分岐の実測、本番用安全機構そのものである。

- **本番用安全機構そのもの。** 本番用ゲートは未実装・未検証であり、本 PR の検証専用パッチは代替ではない。（未解決のままである）
- 1 バイト以外の転送長（length 0、length > 1）、複数バイト転送での他バンク到達。→ **後続 suite で実測済み**（length 1/2/3/4/8、境界跨ぎ、alias、TAM 変更、length 0 の preflight 拒否）。
- スタックページが $FF 以外（例: $F8）のときの TransferStart push 拒否（ガードは $FF ページのときだけ拒否する）。（未解決のままである。#473 の範囲）
- TransferStart 以外の push（PHA/PHP/JSR/IRQ）、および TransferEnd の pop 自体への遮断（pop は対象外）。（TransferEnd の pop の実挙動は #459 で実測済み。pop 自体の遮断は未実装。#473 の範囲）
- 割込み（NMI/IRQ1）が処理中に上記の拒否が起きた場合の挙動、DEC 等の他命令との併用。→ 転送中の IRQ 保留自体は #473 stack-s6 で実測済み（handler は TransferEnd 後に開始）。NMI は pinned コアに該当なし。
- 命令途中から副作用なく安全に中断できるか。→ **方式別に実測済み。** per-byte 拒否は停止ではないこと（5 命令）、全体事前拒否は停止であること（matrix / stack-s6）。ただし mid-transfer の MPR 変更に対する停止は BLOCKED である。
- デバイス起点の RAM 書込み。→ ソース監査は解決済み（25 行。`docs/research/367-device-write-audit.md`）。実コアプローブは未実施のままである。
- 本番 ROM、ROM への書込み、CD 起動、実ゲーム、純正 BIOS との互換性。いずれも対象外。

**ゲートと成果物の状態**

- `#459` のゲートは **BLOCKED** のまま。`BLOCKED_ABI` と `release_eligible=false` は変更していない
  （本 PR は `spec/` を一切変更していない）。
- 検証専用パッチとパッチ済みコアは `tests/integration/patches/`（GPL-3.0）と一時ディレクトリ内にのみ存在し、
  `build/` 以下の成果物・配布物・CI の upload-artifact には含まれない（`make check` の artifact guard が
  「リンク済み実行ファイル・オブジェクトなし」を確認する）。

## per-byte 中途拒否と転送元読みの副作用（本 PR の新規実測、5 命令 × 6 ケース = 30）

`tests/integration/geargrafx_block_midrefusal.py`（`make block-mid-refusal`、`make integration` に含まれる）は、既存の検証専用パッチ（`geargrafx-b49ae82-block-write-guard.patch`）の per-byte フックを、複数バイト転送の境界跨ぎと転送元読みに適用して実測する。abort guard は null のため、全体事前拒否（preflight）は本 suite の対象外である。結論は次のとおりである。

- **per-byte 拒否は命令を停止しない。** length 3 の転送が `$3FFF`/`$4000` ページ境界を跨ぐ場合（`cross`）、5 命令すべてで、拒否されたバイトの Read/Write は副作用なくスキップされるが、転送は継続し、拒否バイトの前後の許可バイトが着地し、TransferEnd が A/X/Y を復元し、S は釣り合い、PC は終了点、P/MPR は不変、VDC AR/R0/R7/SR は不変である。TIA では拒否バイトの次の許可バイトが着地し（交互順序の実測）、TDD では拒否バイトの後に 2 バイトが着地する。したがって方式 B は「許可外バイトだけを届かせない」方式であり、命令全体の停止・原子性はない。
- **転送元読みの副作用は、許可宛先では着地する。** source `$4104`（VDC ステータスレジスタ。読み出しでクリアされる）を WRAM 宛先へ転送する length 1 の場合（`srcread`）、宛先にはステータス値 `0x05` が着地し、ステータスレジスタは `0x05`→`0x00` にクリアされる。宛先しか見ない per-byte ガードでは、この read side effect を防げない。
- **拒否宛先では転送元読みもスキップされる。** source `$4104` を `$FF` ページ宛先（VDC AR）へ転送する場合（`srcreadff`）、ステータスレジスタはプリセットのまま残り、AR は不変である。per-byte フックが Read の前にあることの実測である。
- **対照（unguarded）:** フックなしでは、禁止バイトが VDC AR に着地する（書き込まれた値が 5 ビットにマスクされることまで実測。`$4001` への書込みは本 suite のケースに含まれないため未実測であり、ソースのレジスタ decode から確認できる範囲に留める）。`srcreadff` の対照ではステータス値が AR に着地し、ステータスレジスタがクリアされる。
- **防御方式の成立範囲（本 PR の比較結果）:** A（TransferStart 前の全体拒否）は、転送元・転送先の全バイトを事前に検査できるため、read side effect も含めて副作用前に止めることができる（preflight の実測は matrix suite と #473 stack-s6）。B（per-byte 拒否）は許可外書込みだけを止め、転送は継続する。C（全体停止）は A の性質であり、再開可否は #473 の判定に従う。本番方式の最終選定は #473 の範囲であり、本 PR は比較材料だけを引き渡す。

## 受入条件ごとの状況

| 受入条件 | 状況 | 根拠 |
|----------|------|------|
| 5 命令の途中からの安全な実行中断 | **方式別に実測済み。** per-byte 拒否（B）は停止ではないことが 5 命令で実測済み（`*-cross-armed` 5 件: 拒否バイト前後の許可バイトが着地、A/X/Y 復元、S 釣り合い）。全体事前拒否（A）は matrix suite と #473 stack-s6 で実測済み。mid-transfer の MPR 変更に対する停止は #473 stack-s6 で BLOCKED | `geargrafx_block_midrefusal.py`、`geargrafx_block_transfer_matrix.py`、`geargrafx_stack_s6.py` |
| 書込み先が $FF ページの 1 バイト転送が書込み前に拒否される（負例） | **実コアで実測済み**（`*-forbidden-armed` 5 件 + 対照 `*-forbidden-unguarded` 5 件）。armed は VDC 副作用前に拒否、no-hook RED では副作用が着地。#612当時の30ケース PASS の一部（#613は40ケース） | `tests/integration/geargrafx_block_write_guard.py` |
| 許可域（WRAM）への 1 バイト転送が届く（正例） | **実コアで実測済み**（`*-allowed-armed` 5 件）。書込みが着地 | 同上 |
| TransferStart の A/X/Y push がスタックページ $FF で書込み前に拒否される（#459、負例） | **実コアで実測済み**（`*-stackff-armed` 5 件 + 対照 `*-stackff-unguarded` 5 件）。拒否は VDC AR / R[7] 不変で確認。ただし拒否は「安全な停止」ではない（pop による I/O 読み出し・A/X/Y 破壊・VDC ステータスレジスタのクリアも実測） | `tests/integration/geargrafx_block_write_guard.py`（`PASS issue-459` 行と `BLOCKED issue-459` 行） |
| 拒否後の A/X/Y・S・PC・P・MPR・VDC 状態の最終状態（#459） | **実コアで実測済み**。S は釣り合う（`$04`→`$04`）、PC は終了点、P は不変、MPR 8 本は不変。A/X/Y は I/O 読み出し値で上書きされ、VDC ステータスレジスタは `$05`→`$00` | 同上（「#459 拒否後の実コア追跡」節） |
| TransferStart の push がスタックページ WRAM で届く（#459、正例） | **実コアで実測済み**（`*-stackwram-armed` 5 件）。A/X/Y はスタックから復元され、VDC ステータスレジスタは読まれない（対照実験） | 同上 |
| 複数バイト・他バンク・TIA/TAI の交互順序 | **実コアで実測済み**。length 1/2/3/4/8、境界跨ぎ（`$1FFF`/`$3FFF`/`$7FFF`/`$FFFF`、逆方向）、alias、TAM 変更、length 0 拒否は matrix suite（51 ケース）。TIA/TAI の交互順序と per-byte 境界跨ぎは midrefusal suite（30 ケース）。静的検査は `tools/block_xfer_audit.py` | `tests/host/test_block_xfer_audit.py`、matrix / midrefusal driver |
| 転送元読みの副作用 | **実コアで実測済み**（`*-srcread-*`、`*-srcreadff-*` 各 5 件）。許可宛先では VDC ステータスレジスタのクリアと値の着地、拒否宛先では読み自体のスキップ | `geargrafx_block_midrefusal.py` |
| デバイス起点 RAM 書込みの監査列挙 | **ソース列挙済み（#419、本 PR で解決済み。検索範囲内）**。旧 UNKNOWN 行（MB128、Arcade Card）は CPU 起点に解決。実コアでのデバイス起点書込みのプローブは未実施（UNMEASURED） | 下記、`docs/research/367-device-write-audit.md` |
| 変異テストで偽 PASS が出ないこと | host 変異テスト（静的監査 3 件、midrefusal / matrix の合成行 mutation、device-write fixture の注入変異）に加え、実コア driver の armed 判定に対する mutation が実コア行で PASS | `MutationTest`、各 driver の `mutation_test` / `run_mutation_suite`、`tests/host/test_367_midrefusal.py`、`tests/host/test_367_device_write_audit.py` |

## デバイス起点 RAM 書込み（W17）

### 遮断状態（source audit の範囲だけ）

| 観測対象 | 結果 | 状態 |
|---|---|---|
| block-transfer CPU byte store | patch hook は byte write の前 | PRE_WRITE_GUARDED（verification-only、$FF destination 1-byte probe のみ） |
| block-transfer 全命令 | production pre-execution deny は未接続 | UNGUARDED |
| CD related transfer | 本監査で CPU main WRAM への device write を実測していない | NOT_VERIFIED |
| ADPCM DMA | ADPCM RAM 内の DMA 状態は source にあるが main WRAM write を実測していない | NOT_VERIFIED |
| VDC VRAM/SAT DMA | VDC 内部 VRAM/SAT の経路。CPU main WRAM write ではない | NOT_APPLICABLE |
| timer/IRQ | request/ack は確認したが main WRAM write 経路はこの範囲で未確認 | NOT_VERIFIED |
| backup/Arcade Card / MB128 / CDROM RAM / カード RAM | 起動元をトレース済み。いずれも CPU store 起点の別配列への書込み（デバイス自律書込みではない） | CPU_ORIGINATED（下記） |
| ステート復元・ファイルロード | ホスト起点。ゲスト実行中のゲート対象外 | HOST_ONLY |

この表の `PRE_WRITE_GUARDED` は device write を含まず、#173 の production safety gate が
遮断済みである意味ではない。CPU 以外の各行は実コアで書込み時点を捕捉していないので
PASS にしない。

#419 で pinned Geargrafx（b49ae82）のソースを読み取り、検索語による範囲で列挙した。本 PR で起動元をトレースして解決した（実行なし。詳細は `docs/research/367-device-write-audit.md`）。記録は 25 行:

- デバイス起点（DEV-01、DEV-02、DEV-03）: ADPCM RAM、VRAM、VDC 内部 SAT。検索範囲では いずれも WRAM には届かない（NOT_MAIN_RAM）。
- リセット時の初期化のみ（DEV-04、DEV-05、DEV-06、DEV-07、DEV-08、DEV-09、DEV-10）: WRAM、CDROM RAM、CARD_RAM、Arcade Card RAM、BACKUP_RAM（RESET_ONLY）。
- CPU 起点に解決（DEV-11、CPU-01、CPU-02、CPU-03、CPU-04、CPU-05、CPU-06）: VDC 経由の VRAM 書込み、MB128 RAM（joypad 直列経由）、Arcade Card RAM（mapper 経由）、BACKUP_RAM（bank `$F7`）、WRAM、CDROM RAM、CARD_RAM。いずれも `Memory::Write` またはハードウェアレジスタ書込みを経る CPU store 起点であり、MPR の bank 検査で遮断可能な経路である。
- ホスト起点（HOST-01、HOST-02、HOST-03）: ステート復元と MB128 ファイルロード。ゲスト実行中のゲート対象外である。

限界: 検索語で見つけた箇所に限られ、全ファイルは精読していない。間接書込みは除外できていない。
判定の状態は「列挙済み・遮断可否判定済み（検索範囲内）」であり、「全経路の不存在の証明」ではない。デバイス起点書込みを実コアでプローブした記録はない（`geargrafx_block_write_guard.py` の LIMIT 行も、デバイス起点書込みのプローブがないことを記す。本文の表現は原文の言い換え）。

## 残課題と BLOCKED 理由

BLOCKED 理由:
1. 本番ゲートが未接続。実測したのは検証専用パッチと probe 内のテスト方針（$FF ページ拒否、WRAM 物理 map 限定の preflight）であり、本番用の allowlist / 遮断機構ではない。per-byte 拒否（B）は命令を停止しないことが実測済みであり、全体停止の本番機構は #473 の範囲である。
2. 実行時オペランドの復号（src/dst/length）について、preflight のオペランドページが WRAM でない場合の fail-closed 分岐はソースで確認できるが実測していない。静的検査は入力が既知のときのみ使える。
3. デバイス起点書込みの実コアプローブがない（ADPCM DMA、VRAM DMA、SATB の書込み時点を捕捉した記録はない）。ソース監査では WRAM に届かない経路であることが検索範囲で確認できたが、実測ではない。
4. #459 の拒否経路が安全な停止ではない（実コアで実測）。TransferEnd の pop は一度も書かれていないスタックスロットを $FF ページ（VDC レジスタ）から読み出し、A/X/Y を破壊し、VDC ステータスレジスタをクリアする。命令自体の実行も止まらない。本番用安全機構（スタックページを $FF にマップしない、または TransferEnd の pop まで覆うゲート）が未実装・未検証。

解除条件:
- 実コアで 5 命令それぞれについて、複数バイト・他バンクを含めて許可外書込みが着地しないことを記録する（preflight は matrix suite / #473 stack-s6 で記録済み、per-byte は midrefusal suite で記録済み）。命令全体を副作用なく安全に停止できるかも WSL 実行で記録する（`tools/jsr_observation.py` のスキーマ）。
- TIA の交互順序・長さ 0 の規則は実コアで確認済みであり、過大近似は midrefusal / matrix の実測に置き換えた。残るのはオペランドページ非 WRAM の fail-closed 実測である。
- デバイス起点の各経路の実コアプローブで遮断可否を判定する（別 Issue）。
- #459 について本番用安全機構（スタックページの $FF マップを禁止する、または TransferEnd の pop まで覆うゲート）を実装し、実コアで「拒否後に A/X/Y・S・PC・P・MPR・VDC 状態が保存される」ことを記録する。検証専用パッチの測定結果だけでは解除できない。

次の候補: 命令全体の安全な事前拒否・中断可否の実測（V04 専用の probe モード追加。共有フックは #365 の担当範囲のため、新規ファイルで実装する）。
