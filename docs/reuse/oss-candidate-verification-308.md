# OSS 候補台帳の再検証記録（Issue #308）

- 対象 Issue: [#308](https://github.com/mao2009/oss-pce-cd-bios/issues/308)（Refs のみ。Closes は使わない）
- 台帳本体: [oss-candidate-ledger.md](../oss-candidate-ledger.md)（PR #358 で導入済み。本書は台帳の各 pinned 項目を再取得して照合した記録）
- 開始時の基準: `origin/main` = `94ef337a1c976c1d798ec43c4727c79403612f04`
- 検証日時: **2026-10-29T03:39:22Z**（UTC）。取得方法: `gh api repos/{owner}/{repo}/contents/{path}?ref={sha}` で pinned SHA 指定の内容を取得し、Git blob SHA・サイズ・内容の SHA-256 を計算した。
- 位置づけ: **台帳の検証のみ。** 候補のコード本文は本書・PR にコピーしていない。全候補の disposition は PENDING のまま変更しない。
- 結論: **cleared 0 件**。ライセンス・著作権の未確定項目（UNKNOWN）は §4 に列挙する。

## 1. pinned パスの照合（ファイル単位）

凡例: blob（Git オブジェクト SHA-1）と内容の SHA-256 は**先頭 8 桁**のみ記載する。再取得値は台帳の完全値と照合し、全件一致した（NOT_FOUND を除く）。

| 台帳 ID | リポジトリ @ pinned SHA | パス | blob（先頭8桁） | サイズ（B） | SHA-256（先頭8桁） | 照合結果 |
| --- | --- | --- | --- | --- | --- | --- |
| L-01 | pce-devel/huc @ `54f5c73` | `include/hucc/hucc-math.asm` | 0cee925a | 14021 | e259826d | 一致 |
| L-02 | 同上 | `include/hucc/hucc-systemcard.asm` | 027f8932 | 20230 | 4258b7c3 | 一致 |
| L-02 | 同上 | `include/hucc/hucc-systemcard.h` | f907b4b0 | 5522 | f2477642 | 台帳に記載なし（追加確認） |
| L-03 | 同上 | `include/hucc/core-startup.asm` | aef6da44 | 16736 | fa340fca | 一致 |
| L-04 | 同上 | `include/hucc/vdc.asm` | 68d95e93 | 31186 | 4cc4c903 | 一致 |
| L-05 | 同上 | `include/hucc/joypad.asm` | b3e9639a | 29389 | 7c867b77 | 一致 |
| L-06 | 同上 | `include/hucc/core-kernel.asm` | 129bf9df | 17483 | ae183017 | 一致 |
| L-07 | 同上 | `include/hucc/common.asm` | 04caaa30 | 7421 | c16d337c | 一致 |
| L-07 | 同上 | `include/hucc/vce.asm` | 333a7134 | 14094 | 6a88c2e9 | 一致 |
| L-08 | 同上 | `examples/asm/elmer/include/cdrom.asm` | d8f304ed | 45646 | a1de6902 | 一致 |
| 補 | 同上 | `examples/asm/elmer/LICENSE_1_0.txt` | 36b7cd93 | 1338 | c9bff757 | 台帳 §7 の記載と一致（本文は未読） |
| 補 | 同上 | `LICENSE`（ルート） | 473965af | 2386 | 294d72d6 | 台帳未記載（追加） |
| L-09 | mckayemu/hugo @ `64ed226` | `pcecd.c` | 780eba35 | 20589 | 7ada6b45 | 一致 |
| L-10 | 同上 | `bios.c` | a8c4efab | 29130 | db3f227e | 一致 |
| L-11 | 同上 | `cd.c` | af675c16 | 1188 | 31229a81 | 一致 |
| L-12 | 同上 | `COPYING` | ff408338 | 218 | 7062ee21 | 一致 |
| 補 | 同上 | `AUTHORS` | 456c4e11 | 1188 | a9109bcf | 一致 |
| L-12 | 同上 | `LICENSE.gpl` | — | — | — | **NOT_FOUND**（HTTP 404）。台帳の記載どおり |
| L-12 | 同上 | `LICENSE.bsd-modified` | — | — | — | **NOT_FOUND**（HTTP 404）。台帳の記載どおり |
| L-13 | pcsx-redux/nugget @ `c950e18` | `openbios/cdrom/cdrom.c` | 6ce570ad | 5489 | 312a415e | 一致 |
| L-13 | 同上 | `openbios/cdrom/statemachine.c` | c6ee3bdf | 21997 | b49830ea | 一致 |
| L-13 | 同上 | `openbios/cdrom/helpers.c` | 53343c91 | 3373 | d7ee3bb3 | 一致 |
| 補 | 同上 | `LICENSE` | 490e7cb3 | 1075 | 7e423e1a | 台帳未記載（追加） |
| L-14 | libretro/neocd_libretro @ `b1e04c7` | `src/hlebios.cpp` | 15a7c259 | 78705 | e84e9809 | 一致 |
| L-14 | 同上 | `src/cdromtoc.cpp` | 50cb044d | 24161 | 8c628fff | 一致 |
| L-14 | 同上 | `src/cdromcontroller.cpp` | 2576ca7b | 10710 | 3b4af97b | 一致 |
| 補 | 同上 | `LICENSE.md` | fcf85b87 | 7693 | e16063f1 | 台帳 §2 の記載と一致 |
| L-15 | Cult-of-GBA/BIOS @ `a30e9a9` | `bios_calls/math/division.s` | d8eb105c | 1776 | f0f8f423 | 一致 |
| L-15 | 同上 | `bios_calls/math/sqrt.s` | 236feb4a | 770 | 810d0ba2 | 一致 |
| L-15 | 同上 | `bios_calls/math/arctan.s` | f4a03543 | 4850 | cd61cc12 | 一致 |
| 補 | 同上 | `LICENSE` | fae47d5e | 1068 | 761675b9 | 一致 |
| L-16 | cc65/cc65 @ `71746c8` | `libsrc/runtime/udiv.s` | 4115382c | 1671 | 256c162a | 一致 |
| L-16 | 同上 | `libsrc/runtime/umul16x16r32.s` | cfcf82d9 | 1661 | cac8f1dc | 一致 |
| L-16 | 同上 | `libsrc/runtime/mulax3.s` | 34237960 | 447 | 954d7cdb | 一致 |
| 補 | 同上 | `LICENSE` | 88cacf14 | 808 | 8e87ae93 | 一致（blob は台帳 §2 と一致） |
| L-17 | drhelius/Geargrafx @ `b49ae82` | `src/scsi_controller.cpp` | d622ef87 | 35840 | 2dbb6e14 | 一致 |
| L-17 | 同上 | `src/cdrom.cpp` | f1b2402d | 12934 | b80300d2 | 一致 |
| 補 | 同上 | `LICENSE` | f288702d | 35149 | 3972dc97 | 台帳未記載（追加） |
| L-18 | ares-emulator/ares @ `9408cb4` | `ares/pce/pcd/io.cpp` | 54e6a7f7 | 6249 | 9e3f7da8 | 一致 |
| L-18 | 同上 | `ares/pce/pcd/scsi.cpp` | 35be3b7c | 10269 | 2fa6bd1f | 一致 |
| 補 | 同上 | `LICENSE` | a0edf8fc | 41598 | a1053dec | 台帳未記載（追加） |

## 2. ルート LICENSE の冒頭確認（本文の一部、コピーなし）

| 候補 | pinned の LICENSE / COPYING | 確認した内容 | 台帳との差 |
| --- | --- | --- | --- |
| pce-devel/huc | `LICENSE` | 「SDK は複数の出典に基づき、起源の一部は 1980 年代のコード」という注意書き。明確な一般ライセンス文言が冒頭に無い | 台帳 §2 の「表示なし・UNKNOWN」と整合 |
| pcsx-redux/nugget | `LICENSE` | `MIT License` / `Copyright (c) 2019 PCSX-Redux authors` | 台帳は `2020` 表記の file-level 記録（cdrom.c 等）。repo の LICENSE は 2019 のため、file 単位の表示は別途確認が必要 |
| libretro/neocd_libretro | `LICENSE.md` | LGPL-3.0 全文（冒頭は `lgpl-3.0.txt` の参照） | 台帳と整合 |
| Cult-of-GBA/BIOS | `LICENSE` | `Copyright 2020 - 2021 DenSinH and fleroviux` の MIT 文言 | 台帳と整合 |
| cc65/cc65 | `LICENSE` | zlib 系の免責文言 | 台帳と整合（zlib 系の表示条件は台帳記載） |
| drhelius/Geargrafx | `LICENSE` | GPL-3.0 全文 | 台帳と整合（外部試験のみ） |
| ares-emulator/ares | `LICENSE` | `ares Copyright (c) 2004-2025 ares team, Near et al` | 台帳の「ISC」は既存記録のみ。本検証では ISC 文言までは未確認 |
| mckayemu/hugo | `COPYING` | GPL-2.0 が既定。`pcecd.c` のみ「GPL 互換の modified BSD」の例外。参照先 `LICENSE.gpl` / `LICENSE.bsd-modified` は pin に存在しない（§1） | 台帳と整合 |
| mckayemu/hugo | `pcecd.c` 冒頭 | 3 条項の BSD 系文面（再配布条件・著者名の推奨禁止） | 台帳と整合。著作権者行は無いまま（UNKNOWN） |

## 3. 確認日時付きの候補別判定（検証の範囲）

| ID | 候補 | reuse_mode（台帳） | disposition | ライセンス原文の確認 | NOTICE 義務（採用する場合） | 確信度（ライセンス判定） |
| --- | --- | --- | --- | --- | --- | --- |
| L-01 | HuCC hucc-math.asm | ADAPTED 候補 | PENDING | ヘッダの BSL-1.0 は既存記録。本 pin の全文は examples 側のみ取得 | 著作権表示と BSL-1.0 文の同梱が必要 | 中 |
| L-02〜L-07 | HuCC ほか（VDC・入力・カーネル・推移依存） | REFERENCE_ONLY / ADAPTED 候補 | PENDING | ヘッダの BSL-1.0 / 「Boost 1.0」表記は既存記録。`Boost 1.0` と `BSL-1.0` の同一扱いは UNKNOWN（台帳 L-04） | 同上 | 中 |
| L-08 | HuC elmer cdrom.asm | REFERENCE_ONLY | PENDING | 改変部のみ BSL-1.0、ベースは表示なし | ベースの権利が確定するまで採用不可 | 低 |
| L-09 | Hu-Go! pcecd.c | REFERENCE_ONLY | PENDING | COPYING の例外指定と 3 条項文面を確認。`LICENSE.bsd-modified` は NOT_FOUND | 著作権者表示が無く、条件の全文も pin から確定不可 | 低 |
| L-10 | Hu-Go! bios.c | REJECTED | 提案: RESTRICTED | GPL-2.0 既定（COPYING） | ROM への組込み不可 | 高（組込み不可の判定） |
| L-11 | Hu-Go! cd.c | REJECTED | 提案: RESTRICTED | GPL-2.0 既定（COPYING） | ROM への組込み不可 | 高 |
| L-12 | Hu-Go! COPYING | — | PENDING | 本文確認済み。参照先 2 件が欠落 | 条件の確定に必要 | 中 |
| L-13 | nugget OpenBIOS cdrom | ADAPTED 候補（既存記録は REFERENCE_ONLY） | PENDING | repo LICENSE は MIT（2019）。file 単位の表示は既存記録のみ | MIT の著作権表示と許諾文の保持 | 中 |
| L-14 | NeoCD-Libretro 3 ファイル | REFERENCE_ONLY | PENDING | repo LICENSE.md は LGPL-3.0。file 単位の表示なし | 権利者 UNKNOWN のため確定不可 | 低 |
| L-15 | Cult-of-GBA math | REFERENCE_ONLY | PENDING | repo LICENSE は MIT（2020-2021, DenSinH and fleroviux） | MIT の表示保持 | 中（ARM 命令のため転用不可） |
| L-16 | cc65 runtime 3 ファイル | ADAPTED 候補 | PENDING | repo LICENSE は zlib 系の文言を確認 | zlib 系の表示・改変表示。NOTICE の有無は未確認 | 中 |
| L-17 | Geargrafx（試験用） | REFERENCE_ONLY（外部試験のみ） | PENDING | repo LICENSE は GPL-3.0 | ROM にリンクしない | 高（ROM 組込み不可） |
| L-18 | ares 参照 | REFERENCE_ONLY | PENDING | repo LICENSE の冒頭のみ確認。ISC 文言は未確認 | ISC の表示保持（未確認） | 低〜中 |
| L-19 | MAME / Mednafen / beetle-pce | REJECTED（コード） | PENDING | 未取得（pin 未記録） | — | UNKNOWN |
| L-20 | 元 ROM 依存 IPS パッチ | REJECTED（対象外） | — | 未取得 | — | — |

確信度の定義: 「高」はライセンス文そのものにより組込み不可と判定できるもの、「中」は pin のヘッダと repo LICENSE から方向性は言えるが file 単位の確定が残るもの、「低」は表示や権利者が確認できず UNKNOWN を含むもの。

## 4. UNKNOWN の一覧（cleared に変えない根拠）

1. L-08: elmer `cdrom.asm` のベース（Hudson 原コード）の権利者と表示。
2. L-09: `pcecd.c` の著作権者行（無し）と、modified BSD 例外の条件全文（`LICENSE.bsd-modified` が pin に無い）。
3. L-12: Hu-Go! の `LICENSE.gpl` / `LICENSE.bsd-modified` の欠落（NOT_FOUND）。
4. L-14: NeoCD-Libretro 3 ファイルの file 単位の権利者と表示。
5. L-13: nugget の file 単位の著作権表示（repo LICENSE は 2019、ファイル既存記録は 2020）。
6. L-18: ares のファイル単位のライセンス（ISC）の文言確認。
7. L-19: MAME / Mednafen / beetle-pce の pin 未記録（取得・ハッシュ計算は未実施）。
8. 全候補: NOTICE ファイルの有無（pin ツリー全体の列挙は未実施）。
9. HuCC: `Boost 1.0` と `BSL-1.0` の同一扱い、および `LICENSE_1_0.txt` を ROM 配布物へ含める要否。
10. Hu-Go! `AUTHORS` に挙げられた第三者コンポーネント（unzip、zlib、SDL、SDLnet）のライセンス表示。

UNKNOWN 件数: **10 項目**（上記 1〜10）。

## 5. 検証記録（本 PR）

| 区分 | 結果 | 理由 |
| --- | --- | --- |
| pinned パスの取得と blob・SHA-256 照合（§1） | PASS（台帳記載の全パスが存在し一致。NOT_FOUND は Hu-Go! の 2 件のみ） | 検証日時 2026-10-29T03:39:22Z |
| LICENSE / COPYING の冒頭確認（§2） | PASS（確認範囲は冒頭のみ） | 全文の比較は未実施 |
| 台帳の file 単位の権利者・NOTICE の確定 | NOT_RUN | 人間の provenance 監査が必要 |
| L-19 の取得 | NOT_RUN | pin 未記録のため |
| `make check` / `make test` | NOT_RUN | docs のみの変更。ローカル実行は手順に含めない（CI に委ねる） |
| 実コア（Geargrafx）での検証 | NOT_RUN | 本書は台帳の検証であり、実装の検証ではない |
