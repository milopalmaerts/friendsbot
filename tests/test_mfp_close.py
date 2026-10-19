from unittest.mock import Mock, call
import pytest
from friendsbot.mfp import Client


@pytest.mark.parametrize('position_side,closing_side', [('long','sell'),('short','buy')])
def test_close_quotes_current_remainder_and_supplies_expected_price(position_side,closing_side):
    client=Client(key='fp_test_fixture')
    position={'side':position_side,'size':0.25,'market_id':'hyperliquid|xyz:XYZ100'}
    client._call=Mock(side_effect=[{'data':position},{'data':{'estimated_fill_price':29500,'mid':29501}}, {'data':{'id':'exit','status':'pending'}}])
    result=client.close_position('position','runner-close')
    assert result['data']['id']=='exit'
    assert client._call.call_args_list==[
        call('GET','/v1/positions/position'),
        call('GET','/v1/markets/hyperliquid%7Cxyz%3AXYZ100/quote',{'side':closing_side,'size':0.25}),
        call('POST','/v1/positions/position/close',body={'type':'market','expected_price':29500.0,
             'slippage_tolerance_bps':25,'client_order_id':'runner-close'},idempotent=True)]
    # Omitting size delegates the full remaining reduce-only close to the server.
    assert 'size' not in client._call.call_args.kwargs['body']


def test_close_uses_fresh_mid_if_fill_estimate_missing_and_omits_unset_client_id():
    client=Client(key='fp_test_fixture')
    client._call=Mock(side_effect=[{'data':{'side':'short','size':1,'market_id':'binance|BTCUSDT'}},
                                 {'data':{'mid':81000}}, {'data':{'id':'exit'}}])
    client.close_position('p')
    body=client._call.call_args.kwargs['body']
    assert body['expected_price']==81000
    assert 'client_order_id' not in body


@pytest.mark.parametrize('quote',[{}, {'mid':0},{'mid':-1},{'mid':float('nan')},{'mid':float('inf')}])
def test_invalid_quote_never_submits_a_close(quote):
    client=Client(key='fp_test_fixture')
    client._call=Mock(side_effect=[{'data':{'side':'long','size':1,'market_id':'binance|BTCUSDT'}},{'data':quote}])
    with pytest.raises(ValueError):client.close_position('p')
    assert all(c.args[0]=='GET' for c in client._call.call_args_list)


def test_flat_position_never_gets_an_order():
    client=Client(key='fp_test_fixture')
    client._call=Mock(return_value={'data':{'side':'long','size':0}})
    with pytest.raises(ValueError):client.close_position('p')
    client._call.assert_called_once_with('GET','/v1/positions/p')
