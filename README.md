# futunn_tracker

通过 Futunn 公开组合接口获取股票列表，并使用可配置交易后端自动更新仓位。

## 功能

- 每 `30s` 拉取一次 Futunn 持仓股票代码
- 当股票代码集合变化时，触发调仓
- 每只股票目标金额 = `TOTAL_AMOUNT / 股票数`
- 支持股票白名单：白名单中的股票不会被自动加仓/减仓/移除
- 可选 Telegram 消息推送
- `TRADER` 可选 `ibkr`、`futunn`（`futunn` 当前为占位实现）

## 配置

复制配置模板并修改：

```bash
cp env.sample .env
```

关键参数：

- `TRADER`：交易后端，`ibkr` 或 `futunn`
- `PORTFOLIO_ID`：Futunn 组合 ID
- `TOTAL_AMOUNT`：总资金（USD）
- `STOCK_WHITELIST`：白名单，需添加自己持仓的股票代码，逗号分隔，例如 `AAPL,SOXL`
- `STOP_LOSS_PERCENT`：滑动止损百分比，默认 `3`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`：可选

IB Gateway 相关参数：

- `TWS_USERID`
- `TWS_PASSWORD`
- `TWOFA_DEVICE`
- `TRADING_MODE`（`paper` 或 `live`）

## 启动

`ib-gateway` 已设置为可选 profile：只有在 `ibkr` 模式下才需要启动。

`futunn` 模式（只启动追踪容器）：

```bash
docker compose up --build -d futunn-tracker
```

`ibkr` 模式（同时启动 `ib-gateway`）：

```bash
docker compose --profile ibkr up --build -d
```

> 注意：当 `TRADER=ibkr` 时请使用 `--profile ibkr`，否则不会启动 `ib-gateway`。

兼容方式（等同于 ibkr 模式）：

```bash
docker compose --profile ibkr up --build -d
```

查看日志：

```bash
docker compose logs -f futunn-tracker
docker compose logs -f ib-gateway
```
