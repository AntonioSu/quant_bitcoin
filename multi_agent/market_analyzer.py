"""市场综合分析器

把 MarketData 的所有指标(技术面/资金面/情绪面/链上)精简后丢给 LLM,
得到一份 4H~24H 周期的综合多空研判。

输出:
  - bias:        LONG / SHORT / NEUTRAL
  - confidence:  0~100
  - summary:     一句话研判
  - action:      建议动作
  - key_drivers: 关键驱动因素列表
  - risks:       反向风险列表
  - horizon:     时间周期
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional, Any

from dotenv import load_dotenv

from data_sources.base import DataPoint
from multi_agent.decision_committee import DecisionCommittee
from utils import logger
from utils.common_utils import read_file_prompt
from utils.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), 'prompts')
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
_LAST_ANALYSIS_FILE = os.path.join(_DATA_DIR, 'last_analysis.json')


class MarketAnalyzer:
    """市场综合分析器

    输入: MarketData 实例 (或其 to_dict 结果)
    输出: DataPoint
        - value: confidence (0~100, NEUTRAL 时 0)
        - raw:   完整 JSON 分析结果
    """

    def __init__(self, model_name: Optional[str] = None,
                 memory=None, summarizer=None):
        self.name = "Market Analyzer"
        self.llm = LLMClient(
            model_name=model_name or os.getenv("LLM_MODEL_NAME"),
            key=os.getenv("LLM_API_KEY"),
            api_url=os.getenv("LLM_API_URL"),
            timeout=120,
            # 启用服务端 Prompt Caching：sys_prompt = role + 知识库（多 md 拼接），
            # 不含任何随时间变化的内容（memo / 上次研判 / 最近回顾 都已挪到 user prompt），
            # 因此 sys_prompt 在 cache TTL 内字节稳定，前缀缓存可稳定命中。
            extra_body={
                "caching": {"type": "enabled", "prefix": True},
                "thinking": {"type": "disabled"},
            },
        )
        self.memory = memory            # AnalysisMemory 实例
        self.summarizer = summarizer    # StrategySummarizer 实例
        self._last_analysis: Optional[Dict[str, Any]] = self._load_last_analysis()
        self._static_system_prompt: Optional[str] = None
        self._knowledge_context: Optional[str] = None
        self._committee: Optional[DecisionCommittee] = None
        self._committee_enabled = (
            os.getenv("ENABLE_DECISION_COMMITTEE", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )

    def fetch(self, market) -> DataPoint:
        """对当前 market 数据做一次综合分析

        Args:
            market: core.market_data.MarketData 实例
        """
        if not market.is_ready():
            logger.warning("📊 market 数据未就绪，跳过 AI 分析")
            return self._neutral("market 数据未就绪")

        snapshot = self._build_snapshot(market)
        prompt = self._build_prompt(snapshot)
        analysis = self._analyze(prompt, snapshot=snapshot)

        bias = analysis.get("bias", "NEUTRAL")
        confidence = analysis.get("confidence", 0)
        summary = analysis.get("summary", "")

        # 写入研判记忆
        if self.memory:
            try:
                record_id = self.memory.save_analysis(
                    analysis, snapshot_digest=self._digest_snapshot(snapshot)
                )
                analysis["_memory_id"] = record_id
            except Exception as e:
                logger.warning(f"📝 研判记忆保存失败: {e}")

        # 记录本次结果供下次锚定（同时持久化到磁盘）
        prev_bias = self._last_analysis.get("bias") if self._last_analysis else None
        self._last_analysis = analysis
        self._save_last_analysis(analysis)

        direction_changed = prev_bias and prev_bias != bias and prev_bias != "NEUTRAL"
        change_tag = f" (方向变化: {prev_bias}→{bias})" if direction_changed else ""
        logger.info(
            f"🤖 市场综合分析: {bias} ({confidence}%) — {summary[:50]}{change_tag}"
        )

        return DataPoint(
            value=float(confidence) if bias != "NEUTRAL" else 0.0,
            timestamp=datetime.now(),
            source=self.name,
            raw=analysis,
        )

    @staticmethod
    def _load_last_analysis() -> Optional[Dict[str, Any]]:
        """启动时从磁盘恢复上次研判结果"""
        if not os.path.exists(_LAST_ANALYSIS_FILE):
            return None
        try:
            with open(_LAST_ANALYSIS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(
                f"🔄 恢复上次研判: {data.get('bias')} ({data.get('confidence')}%)"
            )
            return data
        except Exception as e:
            logger.warning(f"加载上次研判失败: {e}")
            return None

    @staticmethod
    def _save_last_analysis(analysis: Dict[str, Any]):
        """将本次研判结果持久化到磁盘"""
        try:
            os.makedirs(os.path.dirname(_LAST_ANALYSIS_FILE), exist_ok=True)
            with open(_LAST_ANALYSIS_FILE, 'w', encoding='utf-8') as f:
                json.dump(analysis, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存上次研判失败: {e}")

    @staticmethod
    def _digest_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
        """把完整 snapshot 压缩成关键字段，减少存储量"""
        keys = [
            "fear_greed", "funding_rate", "top_trader",
            "macd_4h", "rsi_4h", "bollinger_4h", "ma_4h",
            "cvd_6x4h", "etf_flow", "taker_4h", "news",
            "exchange_netflow", "macro", "options", "stablecoin", "mvrv",
        ]
        digest = {}
        for k in keys:
            if k in snapshot:
                v = snapshot[k]
                if k == "news" and isinstance(v, dict):
                    digest[k] = {
                        "score": v.get("score"),
                        "sentiment": v.get("sentiment"),
                        "key_signals": v.get("key_signals", [])[:3],
                        "bullish_factors": v.get("bullish_factors", [])[:3],
                        "bearish_factors": v.get("bearish_factors", [])[:3],
                    }
                    continue
                if isinstance(v, dict):
                    digest[k] = {kk: vv for kk, vv in v.items()
                                 if kk in ("value", "signal", "rate",
                                           "classification", "divergence",
                                           "daily_flow_usd", "sentiment",
                                           "score", "buy_ratio")}
                else:
                    digest[k] = v
        return digest

    @staticmethod
    def _compact_news_factors(factors: Any, limit: int = 3) -> list:
        """Keep enough news provenance for market analysis and later reflection."""
        if not isinstance(factors, list):
            return []

        compacted = []
        for item in factors:
            if len(compacted) >= limit:
                break

            if isinstance(item, str):
                factor = item.strip()
                if factor:
                    compacted.append({"factor": factor})
                continue

            if not isinstance(item, dict):
                continue

            factor = str(item.get("factor") or "").strip()
            if not factor:
                continue

            compacted.append({
                "factor": factor,
                "category": item.get("category"),
                "weight": item.get("weight"),
                "source_authority": item.get("source_authority"),
                "truth_level": item.get("truth_level"),
                "impact_scope": item.get("impact_scope"),
                "score_contribution": item.get("score_contribution"),
                "url": item.get("url"),
            })

        return compacted

    @staticmethod
    def _neutral(reason: str) -> DataPoint:
        return DataPoint(
            value=0.0,
            timestamp=datetime.now(),
            source="Market Analyzer",
            raw={
                "bias": "NEUTRAL",
                "confidence": 0,
                "summary": reason,
                "action": "持仓观望",
                "key_drivers": [],
                "risks": [],
                "horizon": "4H~24H",
                "trend_regime": "UNCLEAR",
                "volatility_regime": "NORMAL_VOL",
            },
        )

    def _build_snapshot(self, market) -> Dict[str, Any]:
        """把 MarketData 精简成 LLM 友好的字典"""
        snap: Dict[str, Any] = {}

        if market.fear_greed:
            raw = market.fear_greed.raw or {}
            snap["fear_greed"] = {
                "value": market.fear_greed.value,
                "classification": raw.get("classification"),
            }

        if market.funding_rate:
            raw = market.funding_rate.raw or {}
            snap["funding_rate"] = {
                "rate": market.funding_rate.value,
                "annual_yield_pct": raw.get("annual_yield"),
            }

        if market.top_trader:
            raw = market.top_trader.raw or {}
            snap["top_trader"] = {
                "long_short_ratio": market.top_trader.value,
                "long_account": raw.get("long_account"),
                "short_account": raw.get("short_account"),
            }

        if market.open_interest:
            raw = market.open_interest.raw or {}
            snap["open_interest"] = {
                "value_usd": raw.get("value_usd"),
                "change_1h_pct": raw.get("change_1h"),
                "change_4h_pct": raw.get("change_4h"),
                "change_24h_pct": raw.get("change_24h"),
            }

        if market.liquidation:
            raw = market.liquidation.raw or {}
            snap["liquidation_1h"] = {
                "total_usd": raw.get("total_usd"),
                "long_liquidation_usd": raw.get("long_liquidation_usd"),
                "short_liquidation_usd": raw.get("short_liquidation_usd"),
                "long_short_ratio": raw.get("long_short_ratio"),
                "total_count": raw.get("total_count"),
            }

        if market.etf_flow:
            raw = market.etf_flow.raw or {}
            snap["etf_flow"] = {
                "daily_flow_usd": raw.get("daily_flow"),
                "flow_3d_usd": raw.get("flow_3d"),
                "flow_7d_usd": raw.get("flow_7d"),
                "cum_flow_usd": raw.get("cum_flow"),
                "streak_days": raw.get("streak_days"),
            }

        if market.news:
            raw = market.news.raw or {}
            snap["news"] = {
                "score": market.news.value,
                "sentiment": raw.get("sentiment"),
                "reasoning": raw.get("reasoning"),
                "key_signals": raw.get("key_signals", [])[:3],
                "bullish_factors": self._compact_news_factors(raw.get("bullish_factors")),
                "bearish_factors": self._compact_news_factors(raw.get("bearish_factors")),
            }

        if market.macd:
            snap["macd_4h"] = {
                "signal": market.macd.signal_type.value,
                "above_zero": market.macd.above_zero,
                "histogram_rising": market.macd.histogram_rising,
                "strength": market.macd.strength,
            }

        if market.rsi:
            snap["rsi_4h"] = {
                "signal": market.rsi.signal_type.value,
                "value": market.rsi.rsi_value,
                "above_center": market.rsi.above_center,
                "trend_strength": market.rsi.trend_strength,
                "strength": market.rsi.strength,
            }

        if market.bollinger:
            snap["bollinger_4h"] = {
                "signal": market.bollinger.signal_type.value,
                "percent_b": market.bollinger.percent_b,
                "bandwidth": market.bollinger.bandwidth,
                "is_squeeze": market.bollinger.is_squeeze,
                "strength": market.bollinger.strength,
            }

        if market.ma:
            snap["ma_4h"] = {
                "signal": market.ma.signal_type.value,
                "trend": market.ma.trend,
                "fast_ma": market.ma.fast_ma,
                "slow_ma": market.ma.slow_ma,
                "price_deviation": market.ma.price_deviation,
                "strength": market.ma.strength,
            }

        if market.volume:
            snap["volume_4h"] = {
                "signal": market.volume.signal_type.value,
                "vol_ratio": market.volume.vol_ratio,
                "obv_trend": market.volume.obv_trend,
                "price_change_pct": market.volume.price_change_pct,
                "strength": market.volume.strength,
            }

        if market.cvd:
            snap["cvd_6x4h"] = {
                "divergence": market.cvd.divergence.value,
                "price_change_pct": market.cvd.price_change_pct,
                "cvd_change_pct": market.cvd.cvd_change_pct,
                "is_valid_signal": market.cvd.is_valid_signal,
            }

        if market.taker:
            taker_dict = market.taker.to_dict()
            snap["taker_4h"] = {
                "buy_btc": taker_dict.get("taker_buy_btc"),
                "sell_btc": taker_dict.get("taker_sell_btc"),
                "buy_ratio": taker_dict.get("taker_buy_ratio"),
            }

        if market.cvd_orderflow:
            raw = market.cvd_orderflow.raw or {}
            retail = raw.get("retail", {})
            medium = raw.get("medium", {})
            large = raw.get("large", {})
            snap["cvd_orderflow_4h"] = {
                "retail_net_usd": retail.get("net_usd"),
                "medium_net_usd": medium.get("net_usd"),
                "large_net_usd": large.get("net_usd"),
                "window_minutes": raw.get("window_minutes"),
                "total_trades": raw.get("total_trades"),
            }

        if market.atr:
            snap["atr_4h"] = {
                "value": market.atr.value,
                "period": market.atr.period,
            }

        # 新增数据维度
        if market.exchange_netflow:
            raw = market.exchange_netflow.raw or {}
            snap["exchange_netflow"] = {
                "netflow_btc": raw.get("netflow_btc"),
                "signal": raw.get("signal"),
            }

        if market.macro:
            raw = market.macro.raw or {}
            snap["macro"] = {
                "dxy_value": raw.get("dxy", {}).get("value") if isinstance(raw.get("dxy"), dict) else None,
                "dxy_trend": raw.get("dxy", {}).get("trend") if isinstance(raw.get("dxy"), dict) else None,
                "m2_change_pct": raw.get("m2", {}).get("change_pct_4w") if isinstance(raw.get("m2"), dict) else None,
                "m2_trend": raw.get("m2", {}).get("trend") if isinstance(raw.get("m2"), dict) else None,
                "signal": raw.get("signal"),
            }

        if market.options:
            raw = market.options.raw or {}
            snap["options"] = {
                "put_call_ratio": raw.get("put_call_ratio"),
                "max_pain": raw.get("max_pain"),
                "price_vs_maxpain_pct": raw.get("price_vs_maxpain_pct"),
                "signal": raw.get("signal"),
            }

        if market.stablecoin:
            raw = market.stablecoin.raw or {}
            snap["stablecoin"] = {
                "total_supply_b": raw.get("total_supply_b"),
                "change_7d_pct": raw.get("change_7d_pct"),
                "change_30d_pct": raw.get("change_30d_pct"),
                "signal": raw.get("signal"),
            }

        if market.mvrv:
            raw = market.mvrv.raw or {}
            snap["mvrv"] = {
                "mvrv_ratio": raw.get("mvrv"),
                "zone": raw.get("zone"),
                "signal": raw.get("signal"),
            }

        snap["last_update"] = (
            market.last_update.isoformat() if market.last_update else None
        )

        return snap

    @staticmethod
    def _build_prompt(snapshot: Dict[str, Any]) -> str:
        """把 snapshot 渲染成 LLM 提示"""
        body = json.dumps(snapshot, ensure_ascii=False, indent=2)
        return (
            "以下是当前 BTC 市场的多维度指标快照（4H 周期为主），"
            "请按照系统提示词的规则做综合多空研判：\n\n"
            f"```json\n{body}\n```"
        )

    def _analyze(
        self,
        prompt: str,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        dynamic_context = self._build_dynamic_context()

        if self._committee_enabled and snapshot:
            try:
                return self._normalize(
                    self._get_committee().run(
                        snapshot=snapshot,
                        dynamic_context=dynamic_context,
                    )
                )
            except Exception as e:
                logger.warning(f"🤖 决策委员会失败，回退单体研判: {e}")

        try:
            return self._analyze_legacy(prompt, dynamic_context=dynamic_context)
        except Exception as e:
            logger.error(f"🤖 LLM 市场分析失败: {e}")
            return {
                "bias": "NEUTRAL",
                "confidence": 0,
                "summary": f"LLM 调用失败: {e}",
                "key_drivers": [],
                "risks": [],
                "horizon": "4H~24H",
                "trend_regime": "UNCLEAR",
                "volatility_regime": "NORMAL_VOL",
                "entry_ok": False,
            }

    def _get_committee(self) -> DecisionCommittee:
        if not self._committee:
            self._committee = DecisionCommittee(
                llm=self.llm,
                prompt_dir=_PROMPT_DIR,
                static_context=self._load_knowledge_context(),
            )
        return self._committee

    def _load_static_system_prompt(self) -> str:
        """Load stable role + knowledge text for prompt caching."""
        if self._static_system_prompt is not None:
            return self._static_system_prompt

        sys_prompt = read_file_prompt(
            os.path.join(_PROMPT_DIR, 'market_analyzer.md')
        )

        knowledge = self._load_knowledge_context()
        if knowledge:
            sys_prompt += "\n\n" + knowledge

        self._static_system_prompt = sys_prompt
        return sys_prompt

    def _load_knowledge_context(self) -> str:
        """Load stable market knowledge without the legacy final-output prompt."""
        if self._knowledge_context is not None:
            return self._knowledge_context

        knowledge_dir = os.path.join(os.path.dirname(__file__), 'knowledge')
        chunks: list[str] = []
        if os.path.isdir(knowledge_dir):
            md_files: list[str] = []
            for root, dirs, files in os.walk(knowledge_dir):
                dirs[:] = [d for d in dirs if d != 'news']
                for fname in files:
                    if fname.endswith('.md'):
                        md_files.append(os.path.join(root, fname))
            for fpath in sorted(md_files):
                chunks.append(read_file_prompt(fpath))

        self._knowledge_context = "\n\n".join(chunks)
        return self._knowledge_context

    def _build_dynamic_context(self) -> str:
        """Build dynamic memory/context text shared by legacy and committee paths."""
        parts: list[str] = []

        # 关键原则：让 sys_prompt 在 cache TTL 内保持字节级稳定，
        # 任何会随时间变化的内容（memo / 上次研判 / 最近回顾）一律放到 user 段。
        if self.summarizer:
            memo = self.summarizer.get_memo_text()
            if memo:
                parts.append(
                    "## 近期策略备忘录（仅供参考，不作为入场决策的阻断依据）\n"
                    "以下是历史复盘经验，作为风险提示参考，但不应阻止在明确趋势中的顺势入场：\n\n"
                    f"{memo}"
                )

        if self._last_analysis:
            last = self._last_analysis
            drivers_text = ""
            if last.get("key_drivers"):
                drivers_text = "\n".join(
                    f"  - [{d.get('side','?')}/{d.get('weight','?')}] {d.get('factor','')}"
                    for d in last["key_drivers"] if isinstance(d, dict)
                )
            section = (
                "## 上次研判结果（请遵守一致性规则）\n"
                f"- 方向: {last.get('bias')}  置信度: {last.get('confidence')}%\n"
                f"- 研判: {last.get('summary', '')}\n"
                f"- 动作: {last.get('action', '')}\n"
            )
            if drivers_text:
                section += f"- 关键驱动:\n{drivers_text}\n"
            section += (
                "\n请对比当前指标与上次研判时的情况。"
                "注意：如果上次为 NEUTRAL，不必强制维持 NEUTRAL——"
                "积极评估是否有足够的顺势信号支持给出方向性建议。"
                "如果需要改变方向，必须在 key_drivers 中明确说明变化原因。"
            )
            parts.append(section)

        if self.memory:
            recent = self.memory.get_recent_with_results(n=3)
            if recent:
                lines = []
                for r in recent:
                    tr = r.get("trade_result", {})
                    ref = r.get("reflection", {})
                    pnl = tr.get("pnl", 0)
                    lesson = ref.get("lesson", "") if ref else ""
                    lines.append(
                        f"- {r.get('bias')} {r.get('confidence')}% → "
                        f"PnL ${pnl:+.2f} ({tr.get('trigger_reason', '?')})"
                        f"{' | 教训: ' + lesson if lesson else ''}"
                    )
                parts.append(
                    "## 最近研判回顾\n"
                    "以下是最近几次研判的实际结果，请参考但不要过度拟合：\n"
                    + "\n".join(lines)
                )

        return "\n\n".join(parts)

    def _analyze_legacy(self, prompt: str, dynamic_context: str = "") -> Dict[str, Any]:
        if dynamic_context:
            prompt += "\n\n" + dynamic_context

        resp = self.llm.chat(
            system_prompt=self._load_static_system_prompt(),
            prompt=prompt,
            usage_tag="[market]",
        )
        parsed = self._parse_json(resp)
        return self._normalize(parsed)

    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0]
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            return {
                "bias": "NEUTRAL",
                "confidence": 0,
                "summary": f"JSON 解析失败: {text[:120]}",
                "key_drivers": [],
                "risks": [],
                "horizon": "4H~24H",
                "trend_regime": "UNCLEAR",
                "volatility_regime": "NORMAL_VOL",
            }

    @staticmethod
    def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
        """字段兜底，避免前端拿到空字段炸"""
        bias = str(data.get("bias", "NEUTRAL")).upper()
        if bias not in ("LONG", "SHORT", "NEUTRAL"):
            bias = "NEUTRAL"

        try:
            confidence = int(data.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        confidence = max(0, min(100, confidence))

        trend = str(data.get("trend_regime", "UNCLEAR")).upper()
        if trend not in ("UP_TREND", "DOWN_TREND", "RANGE", "UNCLEAR"):
            trend = "UNCLEAR"

        vol = str(data.get("volatility_regime", "NORMAL_VOL")).upper()
        if vol not in ("LOW_VOL_COMPRESSION", "NORMAL_VOL",
                       "BREAKOUT_EXPANSION", "HIGH_VOL_EXTREME"):
            vol = "NORMAL_VOL"

        normalized = {
            "bias": bias,
            "confidence": confidence,
            "summary": str(data.get("summary", "")),
            "key_drivers": data.get("key_drivers") or [],
            "risks": data.get("risks") or [],
            "horizon": str(data.get("horizon", "4H~24H")),
            "trend_regime": trend,
            "volatility_regime": vol,
        }

        if "entry_ok" in data:
            normalized["entry_ok"] = bool(data.get("entry_ok"))
        if "invalidations" in data:
            normalized["invalidations"] = data.get("invalidations") or []
        if "committee" in data:
            normalized["committee"] = data.get("committee") or {}

        return normalized


def main():
    """独立测试: 拉一遍真实数据后跑一次分析"""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from core.market_data import refresh_market_data, market

    refresh_market_data()
    analyzer = MarketAnalyzer()
    result = analyzer.fetch(market)

    raw = result.raw or {}
    print(f"\n{'=' * 70}")
    print(f"  研判: {raw.get('bias')}  |  置信度: {raw.get('confidence')}%")
    print(f"  动作: {raw.get('action')}")
    print(f"{'=' * 70}")
    print(f"\n  💡 {raw.get('summary')}")

    if raw.get("key_drivers"):
        print("\n  🎯 关键驱动:")
        for d in raw["key_drivers"]:
            if isinstance(d, dict):
                side = d.get("side", "")
                weight = d.get("weight", "")
                print(f"     [{side}/{weight}] {d.get('factor', '')}")
            else:
                print(f"     - {d}")

    if raw.get("risks"):
        print("\n  ⚠️  风险:")
        for r in raw["risks"]:
            print(f"     - {r}")

    print(f"\n{'=' * 70}\n")


if __name__ == "__main__":
    main()
