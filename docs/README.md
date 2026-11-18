# Documentation hub / ドキュメント索引

[← README](../README.md) · [初めて読む](project-guide.md) · [現在の実装状況](status.md)

> **研究・診断段階です。** 文書内のPASSは明記された実験条件に限ります。DEV実装、System Card真正ABI、商用タイトル起動、release適格性は別の評価軸です。

## まず読む / Start here

| 資料 | 何が分かる？ |
| --- | --- |
| [プロジェクトガイド](project-guide.md) | 目的、現在できること、DEV・実験・互換の違い、用語 |
| [Status](status.md) | 18/81 DEV、0/81 ABI、開発・リリースの状態 |
| [Roadmap](roadmap.md) | 段階別の目標・未達条件 |
| [Project review（11/18）](project-review-2026-11-18.md) | 外部向け視点からの強み・弱み・優先改善 |
| [Compatibility](compatibility.md) | 何をもってゲーム互換性と言えるか |

## 開発 / Build, run, architecture

| テーマ | 資料 |
| --- | --- |
| ビルド・再現性 | [Building](building.md) · [Toolchain](toolchain.md) |
| 実装構造 | [Architecture](architecture.md) · [System Card ROM](system-card-rom.md) |
| 実機相当コアでの検証 | [Geargrafx](emulator-geargrafx.md) · [Tests](testing.md) |
| 実験用ローダー | [PUBLIC IPL](g5-commercial-ipl-path-800.md) · [DEV IPL](dev-ipl-boot.md) |
| ビジュアル運用 | [README visualization maintenance](readme-visuals.md) |

## ABI / APIs

| テーマ | 資料 |
| --- | --- |
| 81スロットの候補 | [API inventory](api-inventory.md) |
| 契約と承認 | [ABI contracts](abi-contracts.md) · [Verification tiers](abi-verification-tiers.md) |
| CD関連の実験 | [CD_BASE](cd-base-contract.md) · [CD_READ](cd-read-contract.md) · [FIRST](cd-read-commercial-first-mode.md) |
| 部分的なDEV実装 | [EX_DSPOFF](ex-dspoff-real-cpu-g5.md) · [EX_GETVER](abi2-getver-observation.md) |

## 観測・証拠 / Evidence

| テーマ | 資料 |
| --- | --- |
| **最新商用実測（2026-11-18）** | [G5 FIRST-mode再観測](g5-commercial-reobservation-2026-11-18.md) |
| 実験の経緯 | [G5 addendum](g5-evidence-2026-11-18.md) · [G5 11/13 snapshot](g5-progress-2026-11-13.md) |
| 結果の読み方 | [Verdict schema](verdict-schema.md) · [Compatibility evidence](compat-evidence-schema.md) |
| コア・割込み・スタック | [Geargrafx contracts](geargrafx-contract-evidence.md) · [MPR/stack](497-mpr1-stack-contract.md) |

## 安全性・来歴 / Safety & provenance

| テーマ | 資料 |
| --- | --- |
| 参照コード・ライセンス | [Provenance](provenance.md) · [OSS audit](oss-audit-systemcard.md) |
| リリースゲート | [Fail-closed invariants](725-failclosed-invariants.md) · [P10 gate verification](725-p10-release-gate-verification.md) |
| エージェントコマンドガード | [Destructive guard](claude-destructive-guard.md) |
| その他の詳細 | `docs/contracts/` · `docs/design/` · `docs/evidence/` · `docs/research/` |

### Snapshot policy

- 「2026-11-06」「2026-11-13」の記録は**過去の固定スナップショット**。上書きして現在の証拠にしません。
- 商用媒体の識別情報、画像、バイト、逆アセンブル、生トレースはGit/PR/公開資料に掲載しません。
- 新たな実装・マージがあれば、まず [status](status.md)、次に [README](../README.md) と [visuals](readme-visuals.md) の静的数値を同期します。
