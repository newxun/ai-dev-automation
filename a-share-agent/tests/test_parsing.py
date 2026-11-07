"""持仓表、交割单的解析，以及按成交推算持仓、对账。"""

import io

import pandas as pd

from ashare_agent.portfolio import Position, parse_holdings_file, positions_from_rows
from ashare_agent.trades import Trade, apply_trades, parse_trades_file, reconcile

THS_TXT = (  # 同花顺「资金股份」导出：GBK、制表符、表头上方有资金信息
    "资金余额:48000.00\t可用金额:48000.00\n"
    "证券代码\t证券名称\t股票余额\t可用余额\t冻结数量\t成本价\t市价\t盈亏\t市值\t股东帐户\n"
    "600111\t北方稀土\t3000\t3000\t0\t38.200\t36.830\t-4110.00\t110490.00\tA123456789\n"
    "300059\t东方财富\t3000\t2000\t0\t20.795\t18.980\t-5445.00\t56940.00\t0123456789\n"
)
TDX_CSV = (  # 通达信：="..." 包裹的代码
    "证券代码,证券名称,证券数量,可卖数量,参考成本价,当前价,参考盈亏\n"
    '="000001",平安银行,6000,6000,11.800,11.640,-960.00\n'
    '="601398",工商银行,200,200,7.800,8.350,110.00\n'
)


def test_ths_txt_gbk():
    r = parse_holdings_file("资金股份.txt", THS_TXT.encode("gbk"))
    got = {p.code: p for p in r.positions}
    assert got["600111"].quantity == 3000 and got["600111"].cost_price == 38.2
    assert got["300059"].available == 2000 and got["300059"].sellable == 2000
    assert "股东帐户" in r.dropped_columns


def test_tdx_quoted_codes():
    r = parse_holdings_file("持仓.csv", TDX_CSV.encode("utf-8-sig"))
    assert [p.code for p in r.positions] == ["000001", "601398"]
    assert r.positions[0].cost_price == 11.8


def test_eastmoney_xlsx():
    df = pd.DataFrame({"代码": ["300750"], "名称": ["宁德时代"], "持仓": [300], "可用": [300], "成本": [268.0], "最新价": [264.06]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    r = parse_holdings_file("持仓.xlsx", buf.getvalue())
    assert r.positions[0].code == "300750" and r.positions[0].quantity == 300


def test_manual_rows_and_merge():
    ps, errs = positions_from_rows([{"code": "600111", "quantity": "1,000", "cost_price": "38.2"}, {"code": "abc", "quantity": 1}])
    assert ps[0].quantity == 1000 and errs
    dup = "证券代码,证券名称,持仓数量,成本价\n600111,北方稀土,1000,38.0\n600111,北方稀土,1000,40.0\n"
    merged = parse_holdings_file("dup.csv", dup.encode()).positions
    assert len(merged) == 1 and merged[0].quantity == 2000 and abs(merged[0].cost_price - 39.0) < 1e-6


def test_trades_skip_non_trades_and_fees():
    csv = ("成交日期,证券代码,证券名称,操作,成交数量,成交均价,发生金额\n"
           "20261105,300059,东方财富,证券买入,1000,19.38,-19385.19\n"
           "20261105,,,银行转证券,0,0,50000\n"
           "20261106,300059,东方财富,证券卖出,500,19.80,9884.90\n")
    r = parse_trades_file("交割单.csv", csv.encode("gbk"))
    assert [(t.side, t.quantity) for t in r.trades] == [("buy", 1000), ("sell", 500)]
    assert all(t.fee_estimated for t in r.trades)
    assert r.trades[1].fee_total == 10.05  # 9900×万2.5 不足 5 元→5 + 印花税 4.95 + 过户费 0.099


def test_apply_trades_and_reconcile():
    prev = [Position("300059", "东方财富", 2000, None, 21.5)]
    trades = [Trade("2026-11-05", "300059", "buy", 1000, 19.38, amount=19380.0, commission=5.19)]
    new, notes = apply_trades(prev, trades)
    assert new[0].quantity == 3000 and abs(new[0].cost_price - (43000 + 19380 + 5.19) / 3000) < 1e-4
    assert reconcile(prev, trades, new) == []
    wrong = [Position("300059", "东方财富", 2500, None, 20.8)]
    assert reconcile(prev, trades, wrong)


def test_holdings_preamble_cash_is_kept_but_account_info_dropped():
    from ashare_agent.portfolio import parse_holdings_file

    text = ("资金账号:3100812345\t客户姓名:王小明\r\n币种:人民币\t可用金额:52,000.50\r\n"
            "证券代码\t证券名称\t股票余额\t可用余额\t成本价\t股东帐户\r\n"
            "600111\t北方稀土\t3000\t3000\t38.200\tA123456789\r\n")
    r = parse_holdings_file("持仓.txt", text.encode("gbk"))
    assert r.cash == 52000.5
    assert r.privacy_hits >= 2
    assert "王小明" not in r.raw.to_string() and "A123456789" not in r.raw.to_string()
