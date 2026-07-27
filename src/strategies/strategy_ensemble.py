"""
strategy_ensemble.py — Strategy Signal Aggregator

Combines signals from all strategies using weighted voting
to produce a final trading decision.
"""

from datetime import datetime
from loguru import logger

from src.strategies.base_strategy import BaseStrategy, Signal, SignalType


class StrategyEnsemble:
    """
    Combines multiple strategy signals into a final trading decision
    using weighted voting.
    """

    def __init__(self, strategies: list[BaseStrategy], threshold: float = 0.6):
        self._strategies = [s for s in strategies if s.enabled]
        self._threshold = threshold
        self._signal_history = []  # For debugging/dashboard

    def evaluate(self, dataframes: dict, symbol: str) -> Signal | None:
        """
        Run all strategies and combine their signals.

        Args:
            dataframes: dict of {strategy_name: DataFrame with indicators}
            symbol: Trading symbol

        Returns:
            Final Signal if above threshold, None otherwise
        """
        signals = []

        for strategy in self._strategies:
            try:
                df = dataframes.get(strategy.name)
                if df is None or df.empty:
                    continue

                signal = strategy.generate_signal(df, symbol)
                signals.append((strategy, signal))

            except Exception as e:
                logger.error("Strategy {} failed for {}: {}",
                             strategy.name, symbol, e)
                continue

        if not signals:
            return None

        # Aggregate signals using weighted voting, ignoring HOLD (neutral) signals
        active_signals = [
            (s, sig) for s, sig in signals if sig.signal_type != SignalType.HOLD]

        if not active_signals:
            return None

        buy_score = 0.0
        sell_score = 0.0
        total_weight = sum(s.weight for s, _ in active_signals)

        best_buy_signal = None
        best_sell_signal = None

        for strategy, signal in active_signals:
            weight = strategy.weight / total_weight if total_weight > 0 else 0
            weighted_confidence = weight * signal.confidence

            if signal.signal_type == SignalType.BUY:
                buy_score += weighted_confidence
                if best_buy_signal is None or signal.confidence > best_buy_signal.confidence:
                    best_buy_signal = signal
            elif signal.signal_type == SignalType.SELL:
                sell_score += weighted_confidence
                if best_sell_signal is None or signal.confidence > best_sell_signal.confidence:
                    best_sell_signal = signal

        # Log signal details
        self._log_signals(symbol, signals, buy_score, sell_score)

        # Store history
        self._signal_history.append({
            "symbol": symbol,
            "time": datetime.now().strftime("%H:%M:%S"),
            "buy_score": buy_score,
            "sell_score": sell_score,
            "signals": [(s.name, sig.signal_type.value, sig.confidence) for s, sig in signals],
        })
        # Keep only last 1000 entries
        if len(self._signal_history) > 1000:
            self._signal_history = self._signal_history[-500:]

        # Check for conflicting signals — skip if both buy and sell are strong
        if buy_score > 0.3 and sell_score > 0.3:
            logger.debug("{}: Conflicting signals (buy={:.2f}, sell={:.2f}) — skipping",
                         symbol, buy_score, sell_score)
            return None

        # Return signal if above threshold
        if buy_score >= self._threshold and best_buy_signal:
            final = Signal(
                signal_type=SignalType.BUY,
                symbol=symbol,
                strategy_name="ensemble",
                confidence=buy_score,
                price=best_buy_signal.price,
                stop_loss=best_buy_signal.stop_loss,
                take_profit=best_buy_signal.take_profit,
                reason=f"Ensemble BUY (score={buy_score:.2f}): " + "; ".join(
                    f"{s.name}={sig.confidence:.2f}" for s, sig in signals if sig.signal_type == SignalType.BUY
                ),
                indicators=best_buy_signal.indicators,
            )
            logger.info("ENSEMBLE SIGNAL: {} {} @ {:.2f} (confidence={:.2f})",
                        symbol, "BUY", final.price, buy_score)
            return final

        if sell_score >= self._threshold and best_sell_signal:
            final = Signal(
                signal_type=SignalType.SELL,
                symbol=symbol,
                strategy_name="ensemble",
                confidence=sell_score,
                price=best_sell_signal.price,
                stop_loss=best_sell_signal.stop_loss,
                take_profit=best_sell_signal.take_profit,
                reason=f"Ensemble SELL (score={sell_score:.2f}): " + "; ".join(
                    f"{s.name}={sig.confidence:.2f}" for s, sig in signals if sig.signal_type == SignalType.SELL
                ),
                indicators=best_sell_signal.indicators,
            )
            logger.info("ENSEMBLE SIGNAL: {} {} @ {:.2f} (confidence={:.2f})",
                        symbol, "SELL", final.price, sell_score)
            return final

        return None

    def _log_signals(self, symbol: str, signals: list, buy_score: float, sell_score: float):
        """Log individual strategy signals for debugging."""
        parts = []
        for strategy, signal in signals:
            if signal.signal_type != SignalType.HOLD:
                parts.append(
                    f"{strategy.name}={signal.signal_type.value}({signal.confidence:.2f})")

        if parts:
            logger.debug(
                "{}: {} | buy_score={:.2f} sell_score={:.2f}",
                symbol, ", ".join(parts), buy_score, sell_score,
            )

    def get_signal_history(self, limit: int = 50) -> list:
        """Get recent signal history for the dashboard."""
        return self._signal_history[-limit:]

    def get_strategies_info(self) -> list[dict]:
        """Get info about active strategies."""
        return [
            {"name": s.name, "weight": s.weight, "enabled": s.enabled}
            for s in self._strategies
        ]
