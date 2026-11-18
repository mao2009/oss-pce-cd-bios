[English project guide](project-guide.md) | **日本語版**

> 英語版 [project-guide.md](project-guide.md) を正本（Canonical / SSOT）とします。内容が異なる場合は英語版を参照してください。

# Project guide / oss-pce-cd-biosとは

[← 日本語README](../readme.jp.md) · [Docs hub](README.md)

## 目的

PC Engine / TurboGrafx-CDの**System Card 3.0相当のソフトウェア機能**を、適切なライセンスのOSSや独自実装で再現することを目指します。対象はBIOS**ソフトウェア**であり、CDドライブ、追加RAM、ADPCM、HuC6280などのハードウェアをエミュレータ側が提供する前提です。

目標は**振る舞いの互換性**であり、市販BIOSのバイナリ一致ではありません。将来は日本向けCD-ROM² / SUPER CD-ROM²タイトルから検証し、機能単位の真正ABI検証、複数エミュレータの照合、ゲームプレイ、セーブ/ロードへと広げます。

## 3つの異なるもの

| | 通常DEV ROM | G5実験用BIOS | 将来の互換BIOS |
| --- | --- | --- | --- |
| 用途 | ルーチン・機器診断 | PUBLIC IPLと実験APIの結合観測 | 市販タイトルへの互換性 |
| API | 18個の開発用ルーチン、63スタブ | ビルダーで選ぶ限定的な実験配線 | 真正ABIを根拠にしたサービス群 |
| 証拠 | ホスト・実コア | 合成媒体＋許可されたローカル実測 | 本番契約・相互運用・ゲーム単位 |
| 一般利用 | いいえ | いいえ | **未実現** |

通常DEVで動いたルーチンが、商用ソフトから呼べると判断してはいけません。実験用BIOSの配線は通常DEVやrelease経路に自動昇格しません。

## 現在できること（2026-11-18 / main fa694281）

- 固定版cc65/Geargrafxでテスト用HuCARDと256KiB DEV ROMを生成し、バイト同一性やメモリ/CPU動作を検査する。
- 18/81の**プロジェクト独自**DEVルーチンを診断する（真正ABI VERIFIEDは0/81）。
- 合成CDでPUBLIC IPLのINFO/BODY読込み、RAM転送、実験的な `$02`、`$03`、`$33` 経路を確認する。
- Owner許可による商用CDのローカル読取り専用実測で、共通停止地点 `$4A` を特定する。

### まだできないこと

- System Cardの代替として市販ゲームを起動すること。タイトル画面到達0、`G5_OBSERVED=false`。
- 81スロットの真正ABIを宣言すること（VERIFIED 0/81、DEV_CALLABLE 0）。
- 配布可能な互換ファームウェアの生成（`release_eligible=false`、`make release`は意図的に拒否）。
- 参照BIOSを無許可に実行・解析すること（JSR許可0/81）。

## 証拠の区分

| 呼称 | 意味 |
| --- | --- |
| SYNTHETIC / DEV | 自作フィクスチャ上で、プロジェクト定義の動作を確認 |
| HISTORICALLY_OBSERVED / CURRENT_OBSERVED | 条件を明示した商用媒体の観測（ゲーム互換性とは別） |
| SIMULATED_NEXT_API | 仮RTS等の診断的操作で見えた**条件付き**後続API |
| ABI VERIFIED | 独立した契約・副作用等の証拠を満たした真正互換性。**現在0/81** |
| G5_OBSERVED | 商用タイトル画面到達の直接確認。**現在false** |
| BLOCKED / NOT_RUN | 条件未充足・未実測。PASSとは呼ばない |

## はじめる

```sh
make setup
make dev-rom
make test
make test-real-core
make check
```

開発環境はLinux/WSL、固定版Geargrafxとcc65を前提とします。私的媒体はセットアップやCIの前提にはせず、別途opt-inとサニタイズが必須です。詳細は [Building](building.md)、[Testing](testing.md)、[Provenance](provenance.md)。

## 長期の確認手順

1. 独自実装・ソース来歴の承認
2. 合成フィクスチャでの実処理と負例・mutation
3. 固定版Geargrafx実コアでcold/warmとレジスタ/メモリ確認
4. 独立した真正ABI証拠・ハードウェア条件の確認
5. 商用ゲーム単位の起動・表示・操作・保存テスト
6. 安全・再現性・ライセンスのrelease gate

「18個」は開発用の実装数であり、上記4以降が済んだ関数の数ではありません。

[Status](status.md) · [Roadmap](roadmap.md) · [Architecture](architecture.md)
