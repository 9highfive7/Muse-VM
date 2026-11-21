# runpod-image-app

RunPodでGPU（ComfyUI）を起動し、ノードを触らずに簡単なフォームから画像生成するための作業フォルダ。

- 本体はMuseのWebアプリ「RunPod かんたん画像生成」（プロンプト／モデル／サイズ／Steps／CFG／Sampler／Seedをフォームで指定し、サーバー経由でComfyUI APIに接続）
- このフォルダには、関連するスクリプト・ワークフローJSON・メモを置く
- 状態：Webアプリは作成済み。実Podでの生成検証はPod起動時に行う
