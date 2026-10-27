"""Audit replay validation: actual partial fills, direction, and temporal ordering."""
import unittest
from backtest.history_audit import replay, stamp, stats


class HistoryAuditTests(unittest.TestCase):
    def row(self, direction='LONG'):
        return dict(amount=2.,entry=100.,direction=direction,risk_usd=20.,
                    _exits=[dict(time='2026-09-01 12:00:00',amount=2.,price=95.,pnl=-10.)])

    def bar(self, time, price):
        return dict(end=stamp(time), close=price)

    def test_no_trigger_retains_ledger_and_fee(self):
        x=replay(self.row(), [], 'tp_1r')
        self.assertEqual(x['pnl'],-10.)
        self.assertAlmostEqual(x['net_5bps'],-10.195)

    def test_partial_exit_not_counted_twice(self):
        row=self.row()
        row['_exits'].insert(0,dict(time='2026-09-01 09:30:00',amount=1.,price=105.,pnl=5.))
        row['_exits'][1].update(amount=1.,pnl=-5.)
        x=replay(row,[self.bar('2026-09-01 10:00:00',110.)],'tp_1r')
        self.assertEqual(x['pnl'],15.)
        self.assertAlmostEqual(x['net_5bps'],15.-415*.0005)

    def test_actual_exit_before_price_observation(self):
        x=replay(self.row(),[self.bar('2026-09-01 13:00:00',120.)],'tp_1r')
        self.assertEqual(x['pnl'],-10.)
        self.assertFalse(x['triggered'])

    def test_peak_activation_uses_only_past_closes(self):
        bars=[self.bar('2026-09-01 09:00:00',99.),self.bar('2026-09-01 10:00:00',110.),self.bar('2026-09-01 11:00:00',99.)]
        x=replay(self.row(),bars,'be_after_1r')
        self.assertEqual(x['pnl'],-2.)
        self.assertTrue(x['triggered'])

    def test_short_profit_sign(self):
        x=replay(self.row('SHORT'),[self.bar('2026-09-01 10:00:00',90.)],'tp_1r')
        self.assertEqual(x['pnl'],20.)

    def test_realized_drawdown(self):
        self.assertEqual(stats([dict(pnl=x) for x in [10,-5,2,-9]])['max_realized_dd_usd'],12)


if __name__=='__main__':
    unittest.main()
