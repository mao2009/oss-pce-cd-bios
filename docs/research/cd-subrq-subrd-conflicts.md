# Issue #271: CD_SUBRQ / CD_SUBRD（$0D、候補 $E027）の出典別裁定

親: #22。対象: スロット `$0D`、候補エントリ `$E027`。本書は調査台帳であり、`spec/`・`docs/api-inventory.md`・`docs/abi-contracts.md`・`docs/status.md` は変更しない。

**結論（要約）**
- 名称は **CD_SUBRQ と CD_SUBRD の二つが出典ごとに分かれる。同一とは断定しない。conflict unresolved。**
- 公開 caller の実例は固定版 HuC（`pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3`）に **0 件**（equate の一行のみ。local `git grep` の結果で、独立の再確認は未実施）。Hu-Go / huexpress は $0D を HLE していない。よって呼出側 ABI は根拠がない。
- 戻り値は Zeograd の同一項目内で二つの説明が食い違う（A=エラー状態 と A=$1807 の複写）。**conflict unresolved。**
- 実 System Card の実行: 未実行（NOT_RUN）。`BLOCKED_ABI` を維持、`release_eligible=false`、`DEV_CALLABLE` なし、VERIFIED・PASS の主張なし。

## 1. 出典と扱い

| ID | 出典（所在） | revision・日付 | ライセンス | 本書での扱い |
| --- | --- | --- | --- | --- |
| Z | Zeograd「The system card」`https://www.zeograd.com/download/pce_bios.html` の CD_SUBRQ 節（HTML 289–307 行） | 取得 2026-10-19、版なし。本文は system card 3.0 を対象と記す。本文ハッシュは未記録（取得本文を本台帳に保存せず、照合できない）。版と本文ハッシュを確定できないため SECONDARY_SOURCE 扱い | 記載なし | 所在と要点の言い換えのみ。引用なし |
| ST | Michael Stum「PC Engine CD-ROM² BIOS Method offsets」`https://www.stum.de/2025/pcenginebiosoffsets/`（表の cd_subrd 行、HTML 192 行） | 2025-07-28 | 本文 CC BY-NC-SA 4.0、掲載コードは MIT（ページ末尾） | アドレス表の数値のみ事実として記録。本文の引用なし |
| MD | Charles MacDonald「TurboGrafx-16 Hardware Notes」の複写 `ducalex/retro-go@05e97e80ef0943404ed0d37671f6ab3074017d55`（コミット）の `retro-core/components/pce-go/docs/pctech.txt`（blob `797845ce11715712a740dc0272fc6ad1fa26c490`。1–5 行、1473–1487 行） | 原典 `cgfm2.emuviews.com`。複写の取得日 2026-10-19 | 「Unpublished work Copyright 2001, 2002」。公開ライセンスなし | 言い換えのみ。文の引用をしない。原典の版は未確認 |
| HC | HuC `pce-devel/huc@54f5c73606c1e7142e8095c727cab2767e2b3cb3` `include/hucc/pcengine.inc` 692 行（equate の行は 481 行） | 固定版 54f5c73606c1e7142e8095c727cab2767e2b3cb3 | BSL-1.0（ファイル冒頭 8 行） | equate の事実のみ |
| HG | Hu-Go `mckayemu/hugo@64ed226da2288738781743eeef228c069e8973a4` `bios.c`（48、180–181、1091–1093 行） | 固定版 64ed226da2288738781743eeef228c069e8973a4 | GPL-2（`COPYING`）。HLE であり BIOS 証拠ではない | 名称と「未処理は元の呼出しに戻す」のみ。HLE 挙動の根拠にしない |
| HX | huexpress `kallisti5/huexpress` `src/engine/bios.c`（41、173–174 行） | 最終更新 `b91fd8e30d5898c8ca9c6c749a5f5106a03e22d0` | GitHub 判定 NOASSERTION（ライセンス不明）。引用しない | 名称の所在のみ |
| GG | Geargrafx `drhelius/Geargrafx@3685f8cf50070a1647dd3536942718a8581d9db5`（コミット、2026-10-05）の `platforms/shared/desktop/gui_debug_constants.h`（168 行） | 最終更新コミット `3685f8cf50070a1647dd3536942718a8581d9db5`。旧 pin `1e52d13f` は commit として解決しないため不採用 | GPL-3.0（GitHub 判定） | デバッガのラベル 0xE027 の所在のみ |

除外: HuC の `include/huc/system.inc` と `examples/asm/elmer/kickc/bios.h` はライセンス未確認のため根拠に使わない（内容は読んでいない）。`include/huc/cdrom.asm` も読まない。

## 2. 出典別の $0D 記述（統合しない）

| 出典 | 名称 | 引数（IN） | 戻り値（OUT） | flags・副作用 | 版・性質 |
| --- | --- | --- | --- | --- | --- |
| Z | CD_SUBRQ（Function #$0D） | 「N/A」 | A: 00 = ok、01 = error。さらに注記で「A = ($227E) = ($1807)」、キャリーは $1803 bit 4 に従う | 説明は「Q サブチャネルを読む」 | 3.0 向け、著者の逆解析の記述 |
| ST | cd_subrd（$E027） | 記載なし | 記載なし | 記載なし | 版ごとの本体アドレスのみ（1.0: $F3B5、2.0: $F34A、2.1/3.0: $F354、TG2.0/3.0: $F36D） |
| MD | CD_SUBRD | 記載なし | 記載なし | $1807 の値を $227E へ複写する。$1803 bit 4 を事前にセットする（著者の説明）。用途は「不明」と明記 | 機器メモ。BIOS 機能の説明 |
| HC | cd_subrd = $00:E027, FUNC | 等号のみ | 等号のみ | 等号のみ | 固定版で呼出し 0 件（54f5c73606c1e7142e8095c727cab2767e2b3cb3 の local `git grep -i` の結果。独立の再確認は未実施。呼出し行なし。定義は equate のみ） |
| HG | CD_SUBRQ（0x0D）。switch に case なし | HLE なし | HLE なし | 未処理の関数は元の BIOS 呼出しに戻す（コメント 1092 行） | 名称定義のみ。挙動は未模擬 |
| HX | CD_SUBRQ（0x0D） | 名称のみ | 名称のみ | 名称のみ | 名称定義のみ |
| GG | CD_SUBRD（0xE027） | ラベルのみ | ラベルのみ | ラベルのみ | デバッガ表示名のみ |

名称の内訳: CD_SUBRQ は Z・HG・HX。CD_SUBRD は ST・HC・MD・GG。

## 3. 項目別の裁定

状態語: `SOURCE_FACT`（下記の定義行）、`SECONDARY_SOURCE`（二次資料〔Zeograd、Stum、MacDonald 複写〕のみが根拠。原典・コードで未確認）、`HYPOTHESIS`（仮説。採用しない）、`UNKNOWN`（出典にない、または矛盾）。`VERIFIED` と `OBSERVED` は本書の範囲で使わない（NOT_RUN のため証跡がない）。

- `SOURCE_FACT`: SOURCE_FACT = a caller-side fact written in a pinned primary source. It is independent of provenance PENDING. It does NOT indicate BIOS callee behavior.
  （訳: 固定版の一次出典に書かれた呼出側の事実。出典の provenance が PENDING かどうかとは独立。BIOS 呼出先の振る舞いを示さない。）出典が第三者の記述のみ、または再実装・開発者の主張である場合は `HYPOTHESIS` とする。ライセンス上の利用可否、呼出先の実測、実装可能性は別の軸であり、SOURCE_FACT はそのいずれも示さない。

| ID | 項目 | 主張（出典） | 状態 | 裁定 |
| --- | --- | --- | --- | --- |
| C1 | 名称（HuC の leg） | HC は $E027 を cd_subrd と定義する（pcengine.inc 481 行）。$0D に CD_SUBRD を当てる記載はない。他の出典の名称は C18 で扱う | SOURCE_FACT | **conflict unresolved**。同一と断定しない |
| C2 | エントリ | $E027 に入口（HC の equate 481 行）。ST、Z の記述は裏付けとして数えない | SOURCE_FACT | 呼出し側の定義の所在のみ。BIOS 側の機能・挙動の証拠ではない |
| C3 | 入力（IN） | 入力なし（Z の「N/A」のみ） | SECONDARY_SOURCE | 単一出典。他の出典に記載なし |
| C4 | 戻り値 A | A = 00 ok / 01 error（Z の OUT）。ただし同項の注記は A = $1807 の複写と記す | UNKNOWN | **conflict unresolved**（Z 内部の矛盾） |
| C5 | キャリーフラグ | $1803 bit 4 が立つとキャリーを立てる（Z の注記のみ）。エラーとの対応は不明 | UNKNOWN | 解釈未確定 |
| C6 | $1807 → A・$227E の複写 | 複写する（Z の注記、MD）。二出典は独立とは限らない | SECONDARY_SOURCE | 独立性は UNKNOWN |
| C7 | 呼出し前の $1803 bit 4 セット | BIOS が事前にセットすると記す（MD） | SECONDARY_SOURCE | 単一出典（著者の説明） |
| C8 | Q サブチャネルの内容を返すか | Z は説明に「Q サブチャネルの読取り」と書く。Z の注記と MD の機構（$1807 の複写）は Q データを示さない | UNKNOWN | **conflict unresolved**。名称の説明と機構が一致しない |
| C9 | 電源投入時の MPR 前提（全 BIOS 共通） | MPR0 = $FF、MPR1 = $F8、MPR7 = $00 を前提とする（ST） | SECONDARY_SOURCE | 全関数の一般記述。$0D 固有ではない |
| C10 | $0D 固有の MPR・SP・作業 RAM の副作用（$227E 以外） | 記載なし | UNKNOWN | 観測が必要 |
| C11 | IRQ への副作用 | 記載なし | UNKNOWN | 観測が必要 |
| C12 | タイムアウト・完了待ち | 記載なし。MD の「$1803 bit 4 を立てる」が待ちを意味するかは不明 | UNKNOWN | 観測が必要 |
| C13 | 公開 caller の ABI（引数の渡し方、戻り値の読取り） | 固定版 HuC に呼出しがない（HC の local `git grep`）。HG、HX は HLE なし | UNKNOWN | 呼出側の根拠がない |
| C14 | HLE による挙動 | HG は CD_SUBRQ を模擬しない。HX は名称のみ | UNKNOWN | 挙動の根拠にならない |
| C15 | 版ごとの本体アドレス（ST の表） | 1.0 から 3.0 まで本体の番地が異なる | SECONDARY_SOURCE | アドレスは ABI ではない |
| C16 | ST の「公式名」主張 | ST は 1.0 マニュアルを持ち公式名を得たと記すが、$0D の名称と出典の対応は示さない | UNKNOWN | マニュアル本文は未入手 |
| C17 | CD_SUBRQ と CD_SUBRD は同じ機構か | C6 の機構を両名称に当てはめる仮説。出典の裏付けなし | HYPOTHESIS | **採用しない。conflict unresolved** |
| C18 | 名称（Zeograd・Hu-Go・huexpress の leg） | Z は $0D を CD_SUBRQ と記す（二次資料）。HG・HX も $0D を CD_SUBRQ と名づけるが、いずれも HLE の再実装で一次出典ではない | SECONDARY_SOURCE | **conflict unresolved**。同一と断定しない。HG・HX は独立の裏付けにはしない |
| C19 | X・Y・P の入出力 | 記載なし。Z、ST、MD、HC、HG、HX、GG のいずれも X・Y・P の扱いを記さない（A と C5 のキャリーのみ言及）。Z の 18 行目は全関数の一般記述のみで $0D 固有の記載はない | UNKNOWN | 観測が必要 |

## 4. 観測計画（未実施。BLOCKED_ABI を維持）

本書の UNKNOWN と conflict を決めるには、私的に入手した System Card による読取り専用のコア観測（未実行）が要る（`AGENTS.md` の私的入力規則）。最低限の観測は次のとおり。

1. $E027 を呼ぶ前後で A、X、Y、P、MPR0–7、$227E、$1803、$1807、SP の全差分を取る（C4、C6、C10）。
2. $1803 bit 4 を立てた場合と落とした場合で、キャリーと A の値を比べる（C5、C7）。
3. 戻り値が Q サブチャネルのバイト列か、$1807 の値かを、既知の Q データを持つ合成ディスクで判定する（C8）。
4. 名称の衝突は、観測結果を両名称の仮説に当てはめて判定する（C1、C17）。

## 5. 限界・未実行範囲

- 実 System Card の実行（未実行）、`./rom/` の読取り（未実行）、BIOS のバイト・逆アセンブルの取込みは一切していない。
- Z、ST、MD はいずれも二次資料。公式マニュアル本文は未入手。
- caller の探索は、54f5c73606c1e7142e8095c727cab2767e2b3cb3 の local `git grep -i`（`cd_subrd`、`cd_subrq`）と `gh search code` による名称検索の範囲に限る。全 GitHub や全リビジョンの網羅ではない。
- 本書は呼出し規約を確定しない。`docs/api-inventory.md` の `$0D` 行の状態（HYPOTHESIS、PENDING、NOT_RUN）は変更していない。
