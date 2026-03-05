# futunn_tracker

通过 Futunn 公开组合接口获取股票列表，并使用可配置交易后端自动更新仓位。

## 功能

- 每 `30s` 拉取一次 Futunn 持仓股票代码
- 当股票代码集合变化时，触发调仓
- 每只股票目标金额 = `TOTAL_AMOUNT / 股票数`
- 支持股票白名单：白名单中的股票不会被自动加仓/减仓/移除
- 可选 Telegram 消息推送
- `TRADER` 可选 `ibkr`、`futunn`（`futunn` 当前未实现）

## 配置

复制配置模板并修改：

```bash
cp env.sample .env
```

关键参数：

- `TRADER`：交易后端，`ibkr` 或 `futunn`(futunn相关代码未实现)
- `PORTFOLIO_ID`：Futunn 组合 ID，默认为指王。
- `TOTAL_AMOUNT`：总资金（USD）
- `STOCK_WHITELIST`：白名单！！！非常重要！！！需添加自己需要保留的已持仓股票代码，逗号分隔，例如 `AAPL,NVDA`。如果没有填写，会出售已持仓股票，从而造成不必要的损失。
- `STOP_LOSS_PERCENT`：滑动止损百分比，默认 `4`
- `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`：可选

IB Gateway 相关参数：

- `TWS_USERID`
- `TWS_PASSWORD`
- `TWOFA_DEVICE`
- `TRADING_MODE`（`paper` 或 `live`）
- `IB_PORT`（默认建议 `4003`，对应 ib-gateway 的 socat 转发端口）
- `IB_TIMEOUT_SECONDS`（等待 IB API 就绪超时，默认 `30` 秒）

## 启动

```bash
./run.sh up
```
如果使用的IBKR，运行后需要在手机上使用IB Key授权登陆。

## 关闭

```bash
./run.sh down
```

## 重启

```bash
./run.sh restart
```

查看日志：

```bash
docker compose logs -f futunn-tracker
docker compose logs -f ib-gateway
```

## 注意事项
- 需要订阅实时市场数据，常用数据有纳斯达克，纽交所，ARCA，BATS。
- 需要确认网络能正常连接富途牛牛和IBKR。