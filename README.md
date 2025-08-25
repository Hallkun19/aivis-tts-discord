# Aivis 読み上げ Bot (Discord)

Aivis Cloud APIを使ってDiscordのテキストチャンネルを音声で読み上げるBotです。  
メッセージ読み上げ、VC入退出通知、辞書による読み替え、ユーザー毎の声・速度・音量設定などをサポートします。

## 機能概要
- テキストチャンネルのメッセージを音声合成してVCで再生
- ユーザーごとの設定（声モデル UUID / 発話速度 / 個人音量）
- サーバーごとの読み上げチャンネル指定・サーバー音量設定
- 辞書機能（単語を読みへ置換）
- VC入退室の読み上げ
- 再生キュー管理（`s` でスキップ）

## 必要要件
- Python 3.8+
- ffmpeg（システムPATH に追加）
- Discord Bot トークン（Botに Message Content Intent と Voice State Intent を有効化）
- Aivis API キー（Aivis Cloud）

## 依存パッケージ
pip でインストール:
```
pip install -r requirements.txt
```

## 環境変数 (.env)
プロジェクトルートに `.env` を作成し、以下を設定します:
```
DISCORD_TOKEN=あなたのDiscordBotトークン
AIVIS_API_KEY=あなたのAivis APIキー
# 任意: デフォルトの音声モデルUUID
AIVIS_MODEL_UUID=a59cb814-0083-4369-8542-f51a29e72af7
```

## セットアップと実行
1. リポジトリをクローン／取得  
2. 依存パッケージをインストール（上記参照）  
3. `.env` を作成してトークン等を設定  
4. ffmpeg がインストールされていることを確認  
5. Bot を起動:
```
python main.py
```

## スラッシュコマンド（概要）
- /vc
  - join：Botをあなたが参加しているVCに参加させ、コマンドを実行したチャンネルの読み上げを開始
  - leave：VCから退出
  - mute / unmute：読み上げの停止 / 再開（サーバー単位）
  - pause / resume：再生の一時停止 / 再開
  - volume [0-200]：サーバー全体の音量設定
- /tts
  - channel [channel]：読み上げ対象のテキストチャンネルを設定
  - queue：再生待ち一覧を表示
- /dict
  - add [word] [reading]：単語と読みを登録
  - remove [word]：辞書から削除
  - list：登録単語一覧表示
- /setting
  - model [model_uuid]：声のモデルを設定
  - speed [rate]：読み上げ速度（0.5〜2.0）
  - volume [0-200]：個人音量
  - view：現在の個人設定確認
  - reset：個人設定をリセット

その他: テキストチャンネルで単独で `s` を送ると再生中の音声とキューをスキップします。

## データ保存
- data/dictionaries.json：サーバーごとの辞書
- data/user_settings.json：ユーザーの個別設定  
これらは Bot 起動時に自動作成/更新されます。

## トラブルシューティング（よくある問題）
- ffmpeg が見つからない：システムに ffmpeg をインストールし PATH に追加してください。  
- Bot がメッセージを読み上げない：Bot が対象チャンネルの閲覧権限、読み上げ先 VC の接続権限、Message Content Intent が有効か確認。  
- Aivis API エラー：AIVIS_API_KEY の値を確認。API の利用制限やサービス障害の可能性も確認してください。  
- 音量が期待通りでない：サーバー音量（/vc volume）と個人音量（/setting volume）の掛け合わせで最終音量が決まります。

## 開発メモ
- main.py 内の DEFAULT_MODEL_UUID を環境変数で上書きできます。  
- デフォルトのデータディレクトリは `data/` です。