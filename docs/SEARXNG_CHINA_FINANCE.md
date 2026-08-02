# SearXNG 中国金融网站

本项目没有把 SearXNG 实例配置放在仓库内，不能直接修改外部实例的 `settings.yml`。当前实现通过 SearXNG 的 `site:` 查询约束加入中国金融网站，并在结果进入评分前做来源分层和排序。

## 已接入的站点

### 法定披露与交易所，A 级

- `cninfo.com.cn`：巨潮资讯，公告、年报、季报、互动易
- `sse.com.cn`：上海证券交易所
- `szse.cn`：深圳证券交易所
- `bse.cn`：北京证券交易所

### 主流财经媒体，B 级

- `eastmoney.com`：东方财富
- `10jqka.com.cn`：同花顺
- `stcn.com`：证券时报
- `cs.com.cn`：中国证券报
- `cnstock.com`：上海证券报
- `yicai.com`：第一财经
- `cls.cn`：财联社
- `jrj.com.cn`：金融界

### 线索来源，C 级

- `xueqiu.com`
- 东方财富股吧及部分聚合页面
- `gw.com.cn`、`dzh.com.cn`

线索来源可以帮助发现订单、价格、产能、主营和市场预期线索，但不会单独确认事实或满足权威交叉验证门槛。

## 查询策略

- F1-F5 定向查询继续走 `SearXNG -> DuckDuckGo MCP -> 公共 DuckDuckGo HTML`。
- 额外查询会定向覆盖披露/交易所站点和主流财经媒体站点。
- 搜索结果先按来源级别排序，正文抓取、日期识别和原有核验规则不变。
- 搜索命中仍显示为 `网络命中（未核验）`；结构化数据优先，不能用搜索未命中反推安全。

## 外部 SearXNG 实例

如果需要让所有 SearXNG 客户端都看到这些站点，应在 SearXNG 实例的 `settings.yml` 中启用对应引擎或配置站点搜索引擎，然后重启实例。本项目不提交外部实例的账号、Cookie 或私有地址。
