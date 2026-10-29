# ADPCM RAM 読取り専用アクセサ（測定パッチ、Issue #295）

契約: `docs/research/295-ad-reset-busy-contract.md` §14.14。ローカルの測定専用コピーにだけ適用する。リポジトリにはソース本体を入れない。

## 上流
- 上流: Geargrafx（GPL）、固定 commit `b49ae82a012eade566d72e9d61c9d95a5caa4da3`。
- 対象ファイル: `src/adpcm.h`、`src/adpcm.cpp` のみ。
- パッチ: `tests/integration/geargrafx_adpcm_ram_accessor.patch`（上流 commit に対する差分、`-p1`）。

## 追加するもの
- `Adpcm::GetRAMConst() const`（`adpcm.cpp` に定義）。戻り値は `const u8*`。指す先は `m_adpcm_ram`（`0x10000` バイト、`Adpcm::kRamSize`）。
- 戻り値は同じ `Adpcm` オブジェクト（コア）の生存期間中だけ有効。ビューであってコピーではない。採取はフレーム境界で全体をコピーする。
- 副作用なし（Clock、ADPCM 状態を変えない）。内部バッファを書き換えない。

## 適用手順（測定専用の新しいディレクトリで行う）
1. 上流 commit `b49ae82a` を、新しいディレクトリに取得する（既存の worktree や clone は使わない）。
2. そのディレクトリの直下で `patch -p1 --dry-run < geargrafx_adpcm_ram_accessor.patch` を実行し、問題がなければ `patch -p1 < ...` を実行する。
3. 改変後のコアは、上流の結果と区別して記録する（契約 §14.14）。

## 検証状況
- 静的確認のみ。上流の手元のコピーに対して `patch --dry-run -p1` を実行し、両ファイルが適用可能であることを確認した（ビルドや実行はしていない）。
- `g++ -fsyntax-only` で、改変後の `adpcm.cpp` が通ることを確認した（probe と同じ include 指定）。
- 非侵襲性の回帰テスト（元のコアと改変後のコアで、同じ固定手順の観測値が一致すること）は未実施。実装の段階で行い、独立レビューで CRITICAL と MAJOR が 0 になるまで測定に使わない。

## 注意（上流にある既存の API）
- 上流には既に `u8* Adpcm::GetRAM()`（`adpcm.h`、`adpcm_inline.h`）があり、公開されている。書込み可能なポインタを返す。このパッチの `GetRAMConst()` は、型で書換えを防ぐための読取り専用の経路である。
