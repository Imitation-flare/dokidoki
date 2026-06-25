---
title: Discord Music Bot
emoji: 🎵
colorFrom: blue
colorTo: purple
sdk: docker
pinned: false
---

# Discord Music Bot

YouTube / Spotify / ニコ動 / Bilibili / Twitter / 添付ファイル対応の音楽Bot。

## セットアップ

1. Hugging Face Spacesでこのリポジトリをフォーク
2. Settings → Variables and secrets で `DISCORD_TOKEN` をシークレットに追加
3. 自動でビルド・起動される

## コマンド

| コマンド | 説明 |
|---------|------|
| `/j` | VCに参加 |
| `/kill` | VCから離脱 |
| `/play <URL or 検索ワード>` | 曲を再生 |
| `/search <タイトル>` | 曲を検索して選択 |
| `/pause` | 一時停止/再開 |
| `/stop` | 停止＆キューリセット |
| `/skip` | 投票スキップ |
| `/fs` | 強制スキップ（DJロール必要） |
| `/djset @role` | DJロールをセット |
| `/restart` | Bot再起動（オーナーのみ） |
