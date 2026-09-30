"""Indian exchange-traded derivatives cost model (NSE index options and futures).

Every rate is configurable because statutory rates change (STT in particular was revised
in recent Union Budgets). Defaults below are illustrative and MUST be checked against the
current NSE/SEBI/broker schedule before any conclusion is drawn about real-money viability.
"""
from dataclasses import dataclass


@dataclass
class CostModel:
    brokerage_per_order: float = 20.0      # flat Rs per executed order (discount broker)
    stt_opt_sell: float = 0.001            # on option premium, sell side
    stt_fut_sell: float = 0.0002           # on futures turnover, sell side
    txn_opt: float = 0.0003503             # exchange transaction charge on option premium turnover
    txn_fut: float = 0.0000183             # on futures turnover
    sebi: float = 10 / 1e7                 # Rs 10 per crore
    gst: float = 0.18                      # on brokerage + transaction + SEBI charges
    stamp_opt_buy: float = 0.00003         # buy side, on premium
    stamp_fut_buy: float = 0.00002         # buy side, on futures turnover
    slippage_ticks: float = 1.0            # extra adverse ticks in the 'full_slip' scenario
    slippage_pct: float = 0.002            # ... or this % of price, whichever is larger
    tick: float = 0.05
    fee_mult: float = 1.0                  # robustness knob: 2.0 = double all statutory costs
    slip_mult: float = 1.0                 # robustness knob for slippage

    def fees(self, price, units, is_buy, instrument="opt"):
        """Statutory + brokerage charges (Rs, positive number) for one executed order."""
        turnover = abs(price * units)
        opt = instrument == "opt"
        stt = (self.stt_opt_sell if opt else self.stt_fut_sell) * turnover if not is_buy else 0.0
        txn = (self.txn_opt if opt else self.txn_fut) * turnover
        sebi = self.sebi * turnover
        gst = self.gst * (self.brokerage_per_order + txn + sebi)
        stamp = (self.stamp_opt_buy if opt else self.stamp_fut_buy) * turnover if is_buy else 0.0
        return self.fee_mult * (self.brokerage_per_order + stt + txn + sebi + gst + stamp)

    def slippage(self, mid):
        return self.slip_mult * max(self.slippage_ticks * self.tick, self.slippage_pct * mid)
