# EX_GETFNT フォント候補のファイル単位監査（#707）

調査日: 2026-11-07。対象は [^194]。

本書は **調査のみ**。フォント本体・抽出グリフはリポジトリへ含めない。
固定した配布形式からの参照のみを記録する。`Shinonome` の模様データ
（ビット列）は本書に引用しない。変換の正例・負例は合成模様のみで設計する
（`docs/design/ex-getfnt-font-storage.md`）。

凡例: 判定は `CANDIDATE` / `CONDITIONAL` / `HOLD`。`CONDITIONAL` は
「法務・Owner の承認と NOTICE を条件に再利用候補」、`HOLD` は
「現状この用途へ進めない」。provenance の `APPROVED` は Owner の明示的
承認でのみ成立し（`docs/provenance.md`）、本書は承認を与えない。
証拠区分 `SOURCE_FACT` / `HYPOTHESIS` / `UNKNOWN` は使わず、本書では
各記述の出典と版を示す。

## 結論と推奨

- 推奨は **東雲フォント Gothic Medium Regular の 12 ドット固定長
  （`shnmk12.bdf`）と 16 ドット固定長（`shnmk16.bdf`）** の段階収録。
  理由: (1) JIS X 0208 前方依存が 12/16 両サイズで揃う、(2) 変換が
  JIS X 0208 の `ENCODING` から直接行われ文字コード変換が薄い、
  (3) ROM 容量は段階収録で現実的、(4) 再利用の許諾文が明快。
  採用にあたっては Owner が該当 tarball 版を承認すること。
- **GNU Unifont 16 ドットのみ** は 16×16 専用に限る `CONDITIONAL`。
  12×12 版は存在せず、Unicode 変換の負担と OFL の扱いが重い。
  既存 2 候補のうち、12×12 を満たすのは東雲のみであるため、
  Unifont を主候補にしない。
- 純正 System Card からフォント抽出はしない。商用ゲーム由来のデータは
  使わない。本書の容量試算は固定した配布ファイルの統計に基づく。

## 東雲フォント（Shinonome）

### 正本と固定版

- パッケージ: `fonts/ja-shinonome`、PKGNAME `ja-shinonome-0.9.11pl1nb5`
  （DISTNAME `shinonome-0.9.11p1`、PKGREVISION 5）。NetBSD pkgsrc の
  公開索引は `ja-shinonome-0.9.11pl1nb5` を示す。維持者は
  `tech-pkg-ja@jp.NetBSD.org`。[^101]
- pkgsrc 正本: `http://openlab.ring.gr.jp/efont/dist/shinonome/`、
  HOMEPAGE `http://openlab.ring.gr.jp/efont/shinonome/`。
  pkgsrc 固定版は GitHub ミラー `NetBSD/pkgsrc` の
  `fonts/ja-shinonome/`（Makefile v1.38、最終変更 2022-06-28）。
  GitHub 上の `commit 05fb1419cc4b257be877cf35160aa6d2bd8f983a`
  を確認した（2022-06-28、`Makefile` の再帰ビルド版上げ）。
  後の `PKGREVISION=5` のみ索引側で進む。確認日 2026-11-07。
- 配布 tarball: `shinonome-0.9.11p1.tar.bz2`（4,814,008 bytes）。
  MASTER_SITES `http://openlab.ring.gr.jp/efont/dist/shinonome/`。
  NetBSD の distfile 保持場所でも取得し、distinfo と照合した。

| 保持方法 | 値（確認日 2026-11-07） |
|---|---|
| `SHA512`（distinfo） | `2af35a4dd741484c9abcd3c2088645c34227307e0472ea7f87487eaf590d7524eb6ab03ab15c9f0330d17b3ede75e0ef2e04ef436b2efa35e99c2a62d5738be3` |
| `BLAKE2s`（distinfo） | `336cf3369a9084d7122d8785d11d548a6a2a7237dd274728e06b18c1847367b1` |
| `SHA-256`（取得ファイル） | `95663c95c92ba5765f63ccbdf033eb93b707be01812a989c548db943479c838f` |

tarball 内の上層は `shinonome-0.9.11/` で 88 エントリ。BDF は 64 本。
ライセンス原文は tarball 内 `shinonome-0.9.11/LICENSE` と `AUTHORS`、
`DESIGN.12` / `DESIGN.16` である。持ち物側で取得した BDF 個別の
`SHA-256` は下記監査表を参照。リポジトリへはコミットしない。

### 対象ファイル単位の監査（東雲）

本節の項目は `docs/provenance.md` のファイル単位関門（URL・commit/tag・
パス・ハッシュ・著作者・個別ライセンス・改変・再配布・NOTICE・ROM
組込み）に対応する。第三者の無関係な紹介記事だけを根拠にしない。
`BDF` の `COPYRIGHT "Public Domain"` 表示だけでも判断しない。

| ファイル | 正本（相対パス・版） | `SHA-256`（tarball 抽出後） | 作者・由来 | ライセンス原文と根拠 | 改変・再配布・NOTICE | ROM 組込み | 判定 |
|---|---|---|---|---|---|---|---|
| `shinonome-0.9.11/LICENSE` | 上記 tarball、pkgsrc `ja-shinonome-0.9.11pl1nb5` | `86fbc28a39b01c4c0368e60b5234508353e860aeb10786057bf8ceb9b280afda`（EUC-JP） | Electronic Font Open Laboratory（efont）、2001 | tarball 内原文（EUC-JP で読む）: 「このアーカイブに含まれるすべてのフォントデータ、ドキュメント、スクリプト類はすべて Public Domain で提供されています。但し、日本に於いては現時点で著作権を放棄することは法律上不可能であり、AUTHORS に列挙されている作者がその権利を行使しないと宣言することで実質的な Public Domain であるとします。自由な改造、他フォーマットへの変換、組込み、再配布を行うことができます。同時に、これらはすべて完全に無保証です。」これが根拠であり、pkgsrc の `LICENSE=public-domain` は派生記録である。日本法の放棄不能を明記した「実質的な Public Domain」注記を欠落させてはならない。 | 改変・変換・組込み・再配布が明示許可。帰属表示の義務は本文にない。ただし無保証・日本法注記をソースと配布物に残す。NOTICE は LICENSE と AUTHORS を併記する。 | 実質 PD の宣言と明示許可により、変換後のビットマップの ROM 埋め込みは許される。作者リスト外の権利の行使宣言は全員分である旨は LICENSE 文通りとする。Owner がこの宣言を了承すること。 | `CONDITIONAL` |
| `shinonome-0.9.11/AUTHORS` | 同上 | `91d7736899f9683aaf47aa33a7ceb78f62316a2316fb95ba44e4a7e0902fae1e` | 12 ドット: 古川泰之（Furukawa.Yasuyuki@fujixerox.co.jp）、中村氏ら。14 ドット: 古川泰之ほか。16 ドット: 古川泰之。18 ドット: 金井氏ほか。mkbold/bit2bdf/mkitalic/bdfmerge、configure の作者も列挙。 | LICENSE の「AUTHORS に列挙されている作者がその権利を行使しない」と結び付く。著作者ごとの個別ライセンス表示は tarball 内にない。 | 作者名保持、ただし条文上の再帰属義務はない。 | 同上 | `CONDITIONAL`（LICENSE と対） |
| `shinonome-0.9.11/bdf/shnmk12.bdf` | tarball 内 `bdf/shnmk12.bdf`、0.9.11p1 | `17b0fa218105027f1d6615af3e5e46c2d0fbb2a6ac2fa2f7e5d39dff92d2116f`（997,967 bytes） | 古川泰之、2000。BDF 冒頭 `COMMENT` は「original is designed by Yasuyuki Furukawa (Public Domain)、Modified and Maintained by /efont/」と明記。`COMMENT (c) /efont/ -- Efont Open Laboratory 2001`。 | `COPYRIGHT "Public Domain"`、tarball LICENSE に帰属。FONT 名 `-Shinonome-Gothic-Medium-R-Normal--12-110-75-75-C-120-JISX0208.1990-0`、`FONTBOUNDINGBOX 12 12 0 -2`、`DWIDTH 12 0`、`BBX 12 12 0 -2`、全グリフ `ENCODING` は JIS X 0208 値（0x2121〜0x7426、6,879 字）。 | 改変・変換・組込み可。上表 LICENSE の NOTS を付す。固定長のみ対象。とくに Bold/Italic は除外（「除外したバリアント」の節）。 | 12×12 格納の直接入力として温度が高い。検証済み統計は `docs/design/ex-getfnt-font-storage.md`。 | `CONDITIONAL` |
| `shinonome-0.9.11/bdf/shnmk16.bdf` | tarball 内 `bdf/shnmk16.bdf`、0.9.11p1 | `9965accd5bdbe03bf9395b8dd26dfa4465f4ffa6e9f447c5cb8258644fa1f468`（1,135,668 bytes） | 古川泰之、2000。BDF `COMMENT` 同上。 | `COPYRIGHT "Public Domain"`、同 LICENSE。FONT 名 `-Shinonome-Gothic-Medium-R-Normal--16-150-75-75-C-160-JISX0208.1990-0`、`FONTBOUNDINGBOX 16 16 0 -2`、全グリフ `DWIDTH 16 0`、`BBX 16 16 0 -2`、JIS X 0208 6,879 字。`DESIGN.16` は「東雲12ドットフォントの16ドット版」とし、一部の記号は X11 標準 `jiskan16` から借用した旨を自述する。 | shnmk12 と同じ。 | 16×16 格納の直接入力。 | `CONDITIONAL` |
| `shinonome-0.9.11/bdf/shnm6x12r.bdf`（半角） | tarball 内 `bdf/shnm6x12r.bdf` | `bd54dc33f9caa588183c161a23f374def17be24d28417040cd277c264e4a07e4` | 古川泰之ほか。JIS X 0201.1976 の ASCII＋半角カタカナ | `COPYRIGHT "Public Domain"`。FONT 名 `-Shinonome-Gothic-Medium-R-Normal--12-110-75-75-C-60-JISX0201.1976-0`、`FONTBOUNDINGBOX 6 12 0 -2`、`ENCODING 0..255`、256 字。 | 本件の 2 バイト SJIS 前提では使わない。半角描画は DEV 別途設計。 | 組込み可否は半角契約の承認待ち。 | `HOLD`（本件の 2 バイト対象外） |
| `shinonome-0.9.11/bdf/shnm8x16r.bdf`（半角） | tarball 内 `bdf/shnm8x16r.bdf` | `26eae5a2a057c5756b26f6343f235c0967996e6c1316eff38b94ccc31396d078` | 同上 | `COPYRIGHT "Public Domain"`。FONT 名 `-Shinonome-Gothic-Medium-R-Normal--16-150-75-75-C-80-JISX0201.1976-0`、`FONTBOUNDINGBOX 8 16 0 -2`、221 字。 | 同上 | 同上 | `HOLD`（同上） |

tarball 内の他の 58 BDF（Bold/Italic/Mincho/Marumoji/Proportional/
14/18 ドット/Latin1 など）は「除外したバリアント」の節で除外する。とくに `shnmk12p.bdf`
（Proportional）はセル幅が可変で、固定 12×12 出力には不向きである。
`shnmk12min.bdf` / `shnmk16min.bdf`（明朝）は大半の漢字をゴシックから
借用する旨が `DESIGN` にあるため、本件では主候補にしない。

判定まとめ（東雲）: 採用対象の 12/16 ドットゴシック 2 ファイルは
`CONDITIONAL`。日本法の注記、由来の継承（下記）を Owner が了承し、
LICENSE + AUTHORS を NOTICE として保持することが条件。BDF 自体を
リポジトリへコミットしない。自動的に承認されたわけではない。

### 由来の継承：要町-改と jiskan16（矛盾の記録）

- `DESIGN.12` は「はじめは GPL の 12 ドットフォント 要町-改の
  フォントをベースに、作者の栗原さんから許可を頂いて個人的に改造し、
  約 6,650 文字のデザイン置き換えと追加を行い、栗原様および元製作者の
  中村様との相談の結果、許可を頂いて Public Domain なフォントとして
  新たにスタートした」と明記する。つまり 12 ドットは GPL 起点の
  再設計・許容の来歴を持つ。LICENSE の PD 宣言はこの許可の
  自己申告に依拠する。個別文字が GPL 由来かどうかは tarball から
  判定できない。Owner はこの来歴を承知で承認するか、別途検討する。
- `DESIGN.16` / BDF 冒頭は「一部記号は X11 標準 jiskan16 から
  そのまま（のデザイン）を利用」と明記する。jiskan16 側の個別
  ライセンス表示は tarball 内にない。shnmk16 の PD 宣言と一体として
  Owner が承知する。
- README の内容物説明は 12/14/16 ドットで JIS X 0201/0208 および
  Latin1 を含むと述べるが、BDF の `CHARSET_REGISTRY` 表示は
  shnmk12 が `JISX0208.1990`、shnmk16 が `JISX0208.1983` で食い違う。
  `DESIGN` は両方とも JISX208.1990 と記す。いずれも JIS X 0208 の
  実体を覆すものではないが、版表示の食い違いとして残す。
  どちらが現行かは `ENCODING` の実体で決める。詳細は
  `docs/design/ex-getfnt-font-storage.md` §3。

## GNU Unifont

### 正本と固定版

- 公式公開元: GNU Unifont（Unifoundry）。最新版は **18.0.01**
  （2026-09-16）。BDF/PCF は Plane 0 のみ、OTF は Plane 0 ほか。
  `unifont_jp` は Plane 0 の日本語変体に加え、Plane 2/3 の
  JIS X 0213 補完を含む（OTF で約 59,000 グリフ）。BDF は Plane 0 のみ。
  二重幅であり、グリフは 8 または 16 幅 × 16 高のビットマップである。[^190]
- 公開元: `https://unifoundry.com/unifont/index.html`、
  ダウンロードは `/pub/unifont/unifont-18.0.01/font-builds/`。
  ライセンスは `https://unifoundry.com/LICENSE.txt` と
  `https://unifoundry.com/OFL-1.1.txt`、サイトの知的財産節に
  「GNU GPLv2+ と GNU Font Embedding Exception、および SIL OFL 1.1 の
  二重ライセンス」と明記する（13.0.04 以降）。[^192][^191]

### 対象ファイル単位の監査（Unifont）

| ファイル | 正本（版・確認日） | `SHA-256` | 作者・由来 | ライセンス原文と根拠 | 改変・再配布・NOTICE | ROM 組込み | 判定 |
|---|---|---|---|---|---|---|---|
| `font-builds/unifont_jp-18.0.01.bdf.gz` | Unifont 18.0.01（2026-09-16）、2026-11-07 確認。`gzip` 1,302,446 bytes、展開後 9,381,575 bytes。CHARS 57,086 行（`STARTCHAR U+xxxx`） | `3a0ddf814f89990e84e229a69efdd5f5d7b15e07302865add8463ff7f6965acc` | BDF 冒頭 `COPYRIGHT` に数千名の寄与者（Roman Czyborra、Paul Hardy、チームほか）を一括列挙。不可視文字・動物アイコンのように強い著作権の寄与を含む旨が公開元の知的財産節にある。 | BDF 冒頭 `COPYRIGHT` は「License: SIL Open Font License version 1.1 and GPLv2+: GNU GPL version 2 or later <http://gnu.org/licenses/gpl.html> with the GNU Font Embedding Exception.」と明記。公開元は OFL 一択でも可能と明示する。OFL 1.1 条文（`OFL-1.1.txt`）が根拠。GPL 部分の例外は文書埋め込み向けであり、本件はファームウェア埋め込みである点に注意。 | OFL を選ぶ場合: Font Software（改変版も含む）は OFL 1.1 で配布しなければならず、他のライセンスでは配布できない。ROM 内に含めた場合、その部分は MIT ライセンスでは再ライセンスしない。[OFL 1.1 条文](https://unifoundry.com/OFL-1.1.txt) より、捆包ソフトウェア自体は別のライセンス可だが、フォント部分は OFL を維持する。[OFL 1.1 15 項の要約] | OFL の条件（引用・ライセンス文保持）で埋め込める。GPL 二重ライセンスを理由に MIT への移行はしない。 | `CONDITIONAL`（16×16 限り） |
| `font-builds/unifont-18.0.01.bdf.gz` | Unifont 18.0.01、同上。`gzip` 1,259,315 bytes | 未計測（JP 版の変体比較用）。使用時は固定し計測する。 | 同上。`unifont` は標準変体。 | 同上 | 同上 | 日本語環境では `unifont_jp` が優先のため、本件では対象外とする。 | `HOLD`（対象外） |
| `font-builds/unifont_jp-18.0.01.otf` | Unifont 18.0.01、同上。5,250,304 bytes | 未計測（BDF 抽出が主経路） | 同上。OTF は Plane 0＋補完。 | 同上 | 同上 | BDF 抽出が実用的なため、OTF は ROM 変換経路としては使わない。 | `HOLD`（対象外） |
| `LICENSE.txt` / `OFL-1.1.txt` | Unifoundry の正本ページ、2026-11-07 確認 | 未計測（条文ページ）。使用時に固定する。 | GNU Project / SIL 側の原文 | OFL/GPLv2+ の条文 | OFL 保持、GPL 例外の選択を記録 | ROM 組込みの根拠 | `CONDITIONAL`（LICENSE 文） |

補足: Unifont の CJK グリフは Wen Quan Yi 系の中国語字源を並行する
旨が公開元の「Plane 0 T-source」記述にある（17.0.04）。個別寄与の
由来は BDF の一括 `COPYRIGHT` に含まれる。第三者の紹介記事だけを
根拠にしない。OFL 選択後に、フォント部分を MIT 部として
再配布しないことが必須である。

判定まとめ（Unifont）: 16×16（BDF）限定で `CONDITIONAL`。
12×12 は存在せず、必要な容量（全 57,086 字で 1,709,808 bytes 行単位
展開後合計、うち 16 幅 49,777 字 + 8 幅 7,309 字）も現実的でない。
Unicode 変換表を用意して段階的に収録する設計でも、主候補には
不向きである（詳細な容量は後述）。

## 比較と容量の骨格
### 容量骨格

次の数値は取得した公開配布ファイルの実測による。東雲の BDF は
`CHARS 6,879`（JIS X 0208、`ENCODING 0x2121–0x7426`）。
ビットマップ総量は 12 ドットで 165,096 bytes（2 bytes/行 × 12 行 ×
6,879）、16 ドットで 220,128 bytes（2 bytes/行 × 16 行 × 6,879）。
全体の詳細は `docs/design/ex-getfnt-font-storage.md`。

- 東雲 12 ドットのみ（非圧縮）: 165,096 bytes。
- 東雲 16 ドットのみ（非圧縮）: 220,128 bytes。
- 東雲 12＋16 両（非圧縮）: 385,224 bytes。現行 256 KiB ROM の
  bank 1–31 空き（253,952 bytes）を超える。
- Unifont 全 Plane 0（57,086 字）: 展開後ビットマップ合計
  1,709,808 bytes（16 幅行 1,592,864 + 8 幅行 116,944）。
  ROM への全収録は不可能。段階収録のみ。
- 圧縮: BDF の空行割合は東雲 12 ドット 3.1%、16 ドット 4.1%と低く、
  空行除去や辞書化では十分に縮まない（エントロピー下限でも
  16 ドットで 6.08 bits/byte、12 ドットで 5.76 bits/byte）。両サイズ
  全字を圧縮で収容することは見込めない。詳細は設計文書。

したがって方式は (A) 非圧縮（単一サイズのみ現実的）、
(B) 圧縮（両サイズ全字でも不足）、(C) 段階収録に限る。
組合せは独立再計算の結果を設計文書に示す。

## 除外したバリアント（第三者無断導入の防止）

- 東雲 Bold/Italic/Mincho/Marumoji/Proportional/14・18 ドット/Latin1:
  本件はゴシック固定長の 12/16 のみ。Bold/Italic は描画性が不同で、
  別字として容量とテストが増える。Mincho/Marumoji は漢字を
  ゴシックから借用しており（`DESIGN`）、由来の重複を避ける。
  Proportional は固定セルでない。14/18 ドットは BIOS の 12/16 モードと
  一致しない。Latin1 は BIOS の 2 バイト SJIS 前提と整合しない。
- Unifont OTF/PCF/HEX/PSF/APL 版: BDF 抽出が最小経路であり、OTF は
  ROM 変換経路として不必要。PSF は APL 用で 512 字限定のため対象外。
- GitHub の再配布ミラー（例: `code4fukui/shinonome-font`）が
  "MIT License" と表示する点は、原典の実質 PD 宣言とは衝突する。
  ミラーの MIT 表示は第三者の表示であり、東雲フォントの根拠として
  引用しない。原典（NetBSD pkgsrc tarball）の LICENSE を正とする。
  ミラーでの SHA 一致は未確認であり、採用時はミラーではなく
  NetBSD の tarball 経路で固定する。

## NOTICE 草案（#708 への送り込み）

以下をソースと配布物へ保持する。ライセンス文の意訳ではなく引用と
要旨の併記とする。Owner 承認前は確定しない。

```text
Shinonome Fonts (0.9.11p1, netbsd pkgsrc ja-shinonome-0.9.11pl1nb5)
  Original design: Yasuyuki Furukawa (2000), Modified and Maintained by
  /efont/ (Electronic Font Open Laboratory, 2001).
  tarball: shinonome-0.9.11p1.tar.bz2,
  SHA-256 95663c95c92ba5765f63ccbdf033eb93b707be01812a989c548db943479c838f
  Included files: bdf/shnmk12.bdf, bdf/shnmk16.bdf
  (SHA-256 in ex-getfnt-font-candidates.md).
  License: Electronic Font Open Laboratory (2001), "Shinonome Font License":
  all font data/documents/scripts in the archive are provided as Public
  Domain in substance; the authors listed in AUTHORS declare non-exercise
  of rights (abandonment of copyright is not possible under Japanese law).
  Free modification, conversion to other formats, embedding and
  redistribution are expressly permitted; provided without warranty.
  Note on provenance: DESIGN.12 records a GPL base font (Youmachi-kai,
  with permission from the author and the original maintainer) and
  replacement of most designs; DESIGN.16 records partial reuse of
  X11 standard jiskan16 symbols. This notice preserves the upstream
  declaration and does not add a separate license.
```

Unifont を将来採用する場合:

```text
GNU Unifont 18.0.01 (Japanese BDF: unifont_jp-18.0.01.bdf.gz,
  SHA-256 <fixed at adoption>)
  Copyright (C) 1998-2026 Roman Czyborra, Paul Hardy, Qianqian Fang,
  Andrew Miller, Johnnie Weaver, David Corbett, et al.
  License: SIL Open Font License 1.1 (see https://unifoundry.com/OFL-1.1.txt)
  and GNU GPLv2+ with GNU Font Embedding Exception.
  This project uses the font under OFL 1.1 only. The font portion remains
  under OFL 1.1 and is not relicensed under MIT.
```

## Owner 判断事項

1. 東雲 2 ファイル（`shnmk12.bdf` / `shnmk16.bdf`、`0.9.11p1`）を
   `CONDITIONAL` から `#708` の実開発へ進めてよいか。とくに要町-改
   （GPL）と jiskan16 由来の継承を了承するかどうか。
2. 日本法の「実質的な Public Domain」注記を、法務上の
   問題なく実務に使えるか。NOTICE 併記で足りるか。
3. Unifont の OFL 保持（MIT との分離）と、12×12 がないことの
   制約を了承するか。主候補から外し、候補から外すかどうか。
4. 参照 tarball の URL と SHA を指名し、再取得とハッシュ照合を
   ビルドに組み込むかどうか。BDF 自体をコミットしない方針は本書で確定。

## 確認記録

- 確認者: 調査担当（統合エージェント）。確認日: 2026-11-07。
- Geargrafx 固定版（`b49ae82a012eade566d72e9d61c9d95a5caa4da3`）は
  `docs/system-card-rom.md` と `tools/dependencies.json` による。
  本書で Geargrafx 実行はしない。実測は NOT_RUN。
- 取得ファイルの SHA-256 は NetBSD distinfo の SHA512/BLAKE2s に
  照合した値と並記した。BDF 個別 SHA-256 は tarball 抽出後に計測した。
- 参考: original URLs:
  `https://cdn.netbsd.org/pub/pkgsrc/current/pkgsrc/fonts/ja-shinonome/`,
  `https://unifoundry.com/unifont/index.html`。

[^101]: NetBSD pkgsrc ja-shinonome（2026-11-07 取得、`https://cdn.netbsd.org/pub/pkgsrc/current/pkgsrc/fonts/ja-shinonome/`）。
  PKGNAME `ja-shinonome-0.9.11pl1nb5`、LICENSE `public-domain` の
  索引表示は派生情報であり、本書の根拠は tarball 内 LICENSE 原文である。
[^190]: Unifont 概要（2026-11-07 取得、`https://unifoundry.com/unifont/index.html`）。二重幅・16 高・JP 版の定義等。
[^191]: OFL 1.1 条文（2026-11-07 取得、`https://unifoundry.com/OFL-1.1.txt`）。
[^192]: Unifont の LICENSE.txt および GPLv2+ 例外文（2026-11-07 取得、`https://unifoundry.com/LICENSE.txt`）。
[^194]: 親 [#42](https://github.com/mao2009/oss-pce-cd-bios/issues/42)、
  本調査 [#707](https://github.com/mao2009/oss-pce-cd-bios/issues/707)、
  後継 [#708](https://github.com/mao2009/oss-pce-cd-bios/issues/708)。
