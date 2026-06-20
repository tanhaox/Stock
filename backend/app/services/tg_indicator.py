"""TG 精准买卖主图指标 V11.2 — numpy/pandas 向量化."""
import warnings
import pandas as pd
import numpy as np
from .tdx_functions import MA, EMA, SMA, REF, HHV, LLV, LLV_VARIABLE, BARSLAST, COUNT, CROSS, STD, IF

# Suppress DataFrame fragmentation warning — TG indicator uses many derived columns intentionally
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning, message='.*highly fragmented.*')
warnings.filterwarnings('ignore', category=RuntimeWarning, message='invalid value')

from app.utils.numpy_utils import safe_rsi


class TGSignalParams:
    """TG 指标可配置参数集. 不同板块使用不同阈值."""
    def __init__(self,
        buy_gain_pct: float = 3.0,       # 买涨幅偏阈值 (%)
        sell_loss_pct: float = 3.0,      # 卖跌幅偏阈值 (%)
        gain_norm_div: float = 0.05,     # 涨幅归一化除数 (5%→1.0)
        loss_norm_div: float = 0.05,     # 跌幅归一化除数
        vol_norm_div: float = 2.0,       # 量比归一化除数
        sell_price_bias_a: float = 5.0,  # 卖价偏A (距高点%)
        sell_price_bias_b: float = 10.0, # 卖价偏B (距高点%)
        big_sell_loss: float = 3.0,      # 大卖刚跌幅 (%)
    ):
        self.buy_gain_pct = buy_gain_pct
        self.sell_loss_pct = sell_loss_pct
        self.gain_norm_div = gain_norm_div
        self.loss_norm_div = loss_norm_div
        self.vol_norm_div = vol_norm_div
        self.sell_price_bias_a = sell_price_bias_a
        self.sell_price_bias_b = sell_price_bias_b
        self.big_sell_loss = big_sell_loss


# 主板参数 (默认)
MAIN_BOARD_PARAMS = TGSignalParams()

# 创业板/科创板参数 (20%涨跌幅, 更高波动)
CHINEXT_PARAMS = TGSignalParams(
    buy_gain_pct=5.0,
    sell_loss_pct=5.0,
    gain_norm_div=0.10,      # 10%→1.0 (原5%)
    loss_norm_div=0.10,
    vol_norm_div=3.0,        # 量比3倍→1.0 (原2倍)
    sell_price_bias_a=8.0,   # 距高点<8% (原<5%)
    sell_price_bias_b=15.0,  # 距高点<15% (原<10%)
    big_sell_loss=5.0,       # 跌幅>5% (原>3%)
)


def _get_board_params(ts_code: str) -> TGSignalParams:
    """根据股票代码返回板块参数."""
    code = ts_code.replace('.SZ', '').replace('.SH', '').replace('.BJ', '')
    if code.startswith('300') or code.startswith('301') or code.startswith('688'):
        return CHINEXT_PARAMS
    return MAIN_BOARD_PARAMS


class TGIndicator:
    def __init__(self, df, capital=None, tg_signal_params=None):
        col_map = {}
        for col in df.columns:
            cl = col.lower()
            if cl in ('open','开盘'): col_map['Open'] = col
            elif cl in ('high','最高'): col_map['High'] = col
            elif cl in ('low','最低'): col_map['Low'] = col
            elif cl in ('close','收盘'): col_map['Close'] = col
            elif cl in ('volume','vol','成交量'): col_map['Volume'] = col
            elif cl in ('date','trade_date'): col_map['Date'] = col
        self.df = df.rename(columns={v:k for k,v in col_map.items()})
        self.df = self.df.sort_values('Date').reset_index(drop=True)
        for col in ['Open','High','Low','Close','Volume']:
            if col not in self.df.columns: raise ValueError(f"缺少列: {col}")
        self.capital = capital if capital else 1e10
        self.p = tg_signal_params

    def _p(self, name, default):
        if self.p is not None and hasattr(self.p, name): return getattr(self.p, name)
        return default

    def _step1_params(self):
        df = self.df
        df['ATR20'] = MA(df['High']-df['Low'], 20)
        df['ATR比'] = df['ATR20'] / REF(df['ATR20'], 20)
        df['自适应N'] = IF(df['ATR比']>1.8, 7, IF(df['ATR比']>1.3, 10, 13)).astype(int)
        df['自适应M'] = IF(df['ATR比']>1.8, 5, IF(df['ATR比']>1.3, 7, 9)).astype(int)
        df['MOM周期'] = IF(df['ATR比']>1.5, 3, 5).astype(int)

    def _step2_moving_averages(self):
        df = self.df
        df['MA5']=MA(df['Close'],5); df['MA10']=MA(df['Close'],10); df['MA20']=MA(df['Close'],20); df['MA60']=MA(df['Close'],60)
        M1=3; df['EMA1']=(df['Close']+df['Low']+df['High'])/3
        df['VAR1']=EMA(df['EMA1'],M1); df['买线']=EMA(df['VAR1'],M1)
        # 最近 60 根 K 线的自适应 N 众数（避免全局 df.mode() 引入未来信息）
        recent_n = df['自适应N'].iloc[-60:]
        N_mode = int(recent_n.mode().iloc[0]) if len(recent_n.mode()) > 0 else 13
        df['卖线']=EMA(df['VAR1'],N_mode)

    def _step3_volume(self):
        df=self.df
        df['换手率']=df['Volume']/self.capital*100
        df['VOL_MA5']=MA(df['Volume'],5); df['VOL_MA20']=MA(df['Volume'],20)
        df['AVG_VOL_5']=df['VOL_MA5']  # for L4/L5升级
        df['量比']=df['Volume']/df['VOL_MA5'].mask(df['VOL_MA5']==0, 1.0); df['AVG_VOL_60']=MA(df['Volume'],60)

    def _step4_tg_momentum(self):
        df=self.df
        # 最近 60 根 K 线的 MOM 周期众数（避免全局 df.mode() 引入未来信息）
        recent_mom = df['MOM周期'].iloc[-60:]
        mom = int(recent_mom.mode().iloc[0]) if len(recent_mom.mode()) > 0 else 5
        mom_close=REF(df['Close'],mom).mask(REF(df['Close'],mom)==0, df['Close']); df['TG动量']=EMA((df['Close']-mom_close)/mom_close.mask(mom_close==0, 1.0)*100, 3)
        df['动量方向']=df['TG动量']>REF(df['TG动量'],1)
        df['动量连续']=df['动量方向']&REF(df['动量方向'],1)

    def _step5_stage_high_low(self):
        df=self.df
        df['STAGE_HIGH']=HHV(df['High'],60); df['STAGE_LOW']=LLV(df['Low'],60)
        sh=df['STAGE_HIGH'].mask(df['STAGE_HIGH']==0, 1.0); df['距高点']=(sh-df['Close'])/sh*100
        sl=df['STAGE_LOW'].mask(df['STAGE_LOW']==0, 1.0); df['距低点']=(df['Close']-sl)/sl*100

    def _step6_macd(self):
        df=self.df
        df['DIFF']=EMA(df['Close'],12)-EMA(df['Close'],26)
        df['DEA']=EMA(df['DIFF'],9); df['MACD柱']=2*(df['DIFF']-df['DEA'])

    def _step7_kdj(self):
        df=self.df
        df['RSV']=(df['Close']-LLV(df['Low'],9))/(HHV(df['High'],9)-LLV(df['Low'],9))*100
        df['RSV'] = df['RSV'].apply(lambda x: safe_rsi(x))
        df['K']=SMA(df['RSV'],3,1); df['D']=SMA(df['K'],3,1); df['J']=3*df['K']-2*df['D']
        # RSI14 — 供 L4/L5 大神仙空因子复合验证使用
        from .tdx_functions import calc_rsi
        df['RSI14'] = calc_rsi(df['Close'], 14)

    def _step8_volatility(self):
        df=self.df
        df['振幅5']=MA((df['High']-df['Low'])/df['Close']*100,5)
        df['振幅60']=MA((df['High']-df['Low'])/df['Close']*100,60)
        denom = REF(STD(df['Volume'],20),5)
        df['波动率比'] = STD(df['Volume'],5) / denom.where(denom > 0, 1.0)
        df['高波动']=(df['波动率比']>1.8)&(df['振幅5']>df['振幅60']*1.5)
        df['天量']=(df['Volume']>df['VOL_MA20']*2.5)|((df['Volume']>df['VOL_MA20']*1.8)&(df['波动率比']>2))
        df['曾天量']=BARSLAST(df['天量']); df['在免疫期内']=df['曾天量']<=8
        df['量能回落']=(df['Volume']<df['VOL_MA5']*0.9)&(df['Volume']<df['VOL_MA20'])
        df['免疫期解禁']=~(df['在免疫期内'])|(df['在免疫期内']&df['量能回落']&(df['Close']>LLV_VARIABLE(df['Low'],df['曾天量'].clip(1,200))))
        df['免疫期解禁']=df['免疫期解禁'].fillna(True)

    def _step9_holiday(self):
        df=self.df
        dates=pd.to_datetime(df['Date']); m=dates.dt.month; d=dates.dt.day
        in_win=((m==4)&(d>=28)&(d<=30))|((m==9)&(d>=26)&(d<=30))|((m==1)&(d>=18)&(d<=23))
        gap=dates.diff().shift(-1).dt.days.abs()
        df['节前效应']=in_win&((gap>1)|(gap.isna()&in_win))
        df['节前量能折扣']=IF(df['节前效应'],0.7,1.0)
        df['节前振幅收紧']=IF(df['节前效应'],0.55,0.75)
        df['节前突破乘数']=IF(df['节前效应'],1.5,1.25)
        df['节前大买J门槛']=IF(df['节前效应'],20,25)
        df['节前大买门槛']=IF(df['节前效应'],4,3)

    def _step10_deviation_buy(self):
        df=self.df
        df['买价偏A']=df['距低点']<1.5; df['买价偏B']=(df['距低点']>=1.5)&(df['距低点']<3.0)
        df['买价偏分']=IF(df['买价偏A'],2,IF(df['买价偏B'],1,0))
        df['买量偏']=(df['量比']>1.2)&(df['Volume']>df['AVG_VOL_60']*0.8)
        df['买量偏']=df['买量偏'].astype(float)*df['节前量能折扣']
        df['买涨幅偏']=(df['Close']-REF(df['Close'],1))/REF(df['Close'],1)*100>self._p('buy_gain_pct',3.0)
        df['买波偏']=df['高波动']&(df['TG动量']>REF(df['TG动量'],3))&(df['Close']>df['MA20'])
        df['买入维度累']=df['买价偏分'].astype(int)+df['买量偏'].astype(int)+df['买涨幅偏'].astype(int)+df['买波偏'].astype(int)

    def _step11_deviation_sell(self):
        df=self.df
        df['卖价偏A']=df['距高点']<self._p('sell_price_bias_a',5.0); df['卖价偏B']=(df['距高点']>=self._p('sell_price_bias_a',5.0))&(df['距高点']<self._p('sell_price_bias_b',10.0))
        df['卖价偏分']=IF(df['卖价偏A'],2,IF(df['卖价偏B'],1,0))
        df['卖量偏']=(df['量比']>1.4)&(df['Volume']>df['AVG_VOL_60']*1.5)
        df['卖量偏']=df['卖量偏'].astype(float)*df['节前量能折扣']
        df['卖跌幅偏']=(df['Open']-df['Close'])/df['Open']*100>self._p('sell_loss_pct',3.0)
        df['卖波偏']=df['高波动']&(df['TG动量']<REF(df['TG动量'],3))&(df['Close']<df['MA20'])
        df['卖出维度累']=df['卖价偏分'].astype(int)+df['卖量偏'].astype(int)+df['卖跌幅偏'].astype(int)+df['卖波偏'].astype(int)

    def _step12_basic_signals(self):
        df=self.df
        df['TG动量转强']=df['TG动量']>REF(df['TG动量'],1)
        df['TG动量买门槛']=df['TG动量']>-5
        df['买线_上穿_卖线']=CROSS(df['买线'],df['卖线'])&df['TG动量转强']&df['TG动量买门槛']
        df['动量连续向上']=df['TG动量转强']&(df['TG动量']>REF(df['TG动量'],2))&(df['Close']>REF(df['Close'],1))&(df['J']<40)&df['TG动量买门槛']
        df['低距量比']=df['TG动量转强']&(df['距低点']<3)&(df['量比']>1.2)&df['TG动量买门槛']
        df['买方向']=df['买线_上穿_卖线']|df['动量连续向上']|df['低距量比']
        df['卖线_上穿_买线']=CROSS(df['卖线'],df['买线'])&(df['TG动量']<REF(df['TG动量'],1))
        df['动量连续向下']=(df['TG动量']<REF(df['TG动量'],1))&(df['TG动量']<REF(df['TG动量'],2))&(df['Close']<REF(df['Close'],1))&(df['J']>50)
        df['高距量比']=(df['TG动量']<REF(df['TG动量'],1))&(df['距高点']<5)&(df['量比']>1.4)
        df['卖方向']=df['卖线_上穿_买线']|df['动量连续向下']|df['高距量比']
        df['距上次买']=BARSLAST(df['买方向'])
        df['快翻转卖']=(df['距上次买']<=3)&(df['Close']<REF(df['Close'],1))&(df['TG动量']<0)&(df['J']>35)
        df['卖方向有效']=df['卖方向']|df['快翻转卖']

    def _step13_strength_scores(self):
        df=self.df
        df['量比归一']=(df['量比']/self._p('vol_norm_div',2.0)).clip(0,1)
        df['涨幅归一']=((df['Close']/REF(df['Close'],1)-1)/self._p('gain_norm_div',0.05)).clip(0,1)
        df['距低归一']=(1-df['距低点']/10.0).clip(0,1)
        df['买入基础强度']=(df['量比归一']+df['涨幅归一']+df['距低归一'])/3
        df['动量因子买']=IF(df['TG动量']>0,1.2,IF(df['TG动量']>-2,1.0,IF(df['TG动量']>-5,0.7,0.4)))
        df['买入强度']=(df['买入基础强度']*df['动量因子买']).clip(0,1)
        df['跌幅归一']=((REF(df['Close'],1)-df['Close'])/df['Close']/self._p('loss_norm_div',0.05)).clip(0,1)
        df['距高归一']=(1-df['距高点']/10).clip(0,1)
        df['卖出强度']=(df['量比归一']+df['跌幅归一']+df['距高归一'])/3

    def _step14_strength_history(self):
        df=self.df; n=len(df)
        v1a=np.zeros(n);v2a=np.zeros(n);vs1a=np.zeros(n);vs2a=np.zeros(n)
        pv1=pv2=ps1=ps2=0.0
        bs=df['买方向'].values; ss=df['卖方向有效'].values
        bst=df['买入强度'].values; sst=df['卖出强度'].values
        for i in range(n):
            if bs[i]: pv2=pv1; pv1=bst[i]
            v1a[i]=pv1; v2a[i]=pv2
            if ss[i]: ps2=ps1; ps1=sst[i]
            vs1a[i]=ps1; vs2a[i]=ps2
        df['V买1']=v1a;df['V买2']=v2a;df['V卖1']=vs1a;df['V卖2']=vs2a
        df['买历史强度均值']=IF(COUNT(df['买方向'],60)>=2,(df['V买1']+df['V买2'])/2,0.5)
        df['卖历史强度均值']=IF(COUNT(df['卖方向有效'],60)>=2,(df['V卖1']+df['V卖2'])/2,0.5)

    def _step15_breakthrough(self):
        df=self.df
        df['买突破']=df['买方向']&(df['买入强度']>df['买历史强度均值']*df['节前突破乘数'])&(df['买入强度']>0.6)
        df['突破升级有效']=df['买突破']&(df['距低点']<10)
        df['卖突破']=df['卖方向有效']&(df['卖出强度']>df['卖历史强度均值']*1.35)&(df['卖出强度']>0.65)

    def _step16_stabilization(self):
        df=self.df
        df['企稳加分']=df['买方向']&(df['振幅5']<df['振幅60']*df['节前振幅收紧'])&(df['距低点']<10)&(df['Close']>REF(df['Close'],5))&(df['TG动量']>REF(df['TG动量'],5))

    def _step17_major_signals(self):
        df=self.df
        df['KDJ高位死叉']=(df['J']>75)&(df['K']<df['D'])&REF(df['K']>=df['D'],1)
        df['大卖刚']=df['卖方向有效']&(df['距高点']<5)&df['KDJ高位死叉']&(df['MACD柱']<0)&(df['DIFF']<df['DEA'])&((df['Open']-df['Close'])/df['Open']*100>self._p('big_sell_loss',3.0))
        df['大买刚']=df['买方向']&(df['买入维度累']>=df['节前大买门槛'])&(df['J']<df['节前大买J门槛'])&(df['MACD柱']>REF(df['MACD柱'],1))&(df['TG动量']>-2)&(df['J']<60)

    def _step18_level_determination(self):
        df=self.df
        df['层级买']=IF(df['买入维度累']<=1,1,IF(df['买入维度累']<df['节前大买门槛'],2,3))
        df['层级卖']=IF(df['卖出维度累']<=1,1,IF(df['卖出维度累']<=2,2,3))
        df['层级买升']=IF(df['买方向']&(df['企稳加分']|df['突破升级有效'])&(df['层级买']<3)&~(df['节前效应']&(df['层级买']>=2)),df['层级买']+1,df['层级买'])
        df['层级卖升']=IF(df['卖方向有效']&df['卖突破']&(df['层级卖']<3)&~df['大卖刚'],df['层级卖']+1,df['层级卖'])
        df['层级买终']=IF(df['大买刚'],3,IF(df['买方向']&(df['层级买升']==3)&(df['J']<60),3,IF(df['买方向']&(df['层级买升']==3),2,IF(df['买方向'],df['层级买升'],0)))).astype(int)
        df['层级卖终']=IF(df['大卖刚'],3,IF(df['卖方向有效'],df['层级卖升'],0)).astype(int)

        # ── v4.8: L4/L5 升级 — 大神仙空因子复合验证 ──
        # L4 = L3 + 均线多头排列 (close>MA5>MA10>MA20) + RSI>55
        df['均线多头'] = (df['Close'] > df['MA5']) & (df['MA5'] > df['MA10']) & (df['MA10'] > df['MA20'])
        df['RSI强'] = df['RSI14'] > 55
        df['MACD强'] = df['MACD柱'] > 0
        # L4 条件: 已是 L3 + 至少 2/3 (均线多头/RSI强/MACD强)
        df['大神仙加分'] = df['均线多头'].astype(int) + df['RSI强'].astype(int) + df['MACD强'].astype(int)
        df['层级买L4'] = IF(df['买方向'] & (df['层级买终'] >= 3) & (df['大神仙加分'] >= 2), 4, 0).astype(int)

        # L5 = L4 + 量价配合 (成交量 5日均 > 20日均)
        df['量价配合'] = df['AVG_VOL_5'] > df['AVG_VOL_60'] * 0.8
        df['层级买L5'] = IF(df['层级买L4'] >= 4, 5, 0).astype(int)

        # 最终层级 = max(旧3级, L4, L5)
        df['层级买终'] = IF(df['层级买L5'] >= 5, 5,
                           IF(df['层级买L4'] >= 4, 4, df['层级买终'])).astype(int)

        df['TG_买入信号']=df['买方向']&(df['层级买终']>0); df['TG_买入层级']=df['层级买终']
        df['TG_卖出信号']=df['卖方向有效']&(df['层级卖终']>0); df['TG_卖出层级']=df['层级卖终']
        df['TG_快翻转']=df['快翻转卖']&~df['卖方向']
        df['TG_买入强度']=df['买入强度']; df['TG_卖出强度']=df['卖出强度']

    def compute(self):
        self._step1_params(); self._step2_moving_averages(); self._step3_volume()
        self._step4_tg_momentum(); self._step5_stage_high_low(); self._step6_macd()
        self._step7_kdj(); self._step8_volatility(); self._step9_holiday()
        self._step10_deviation_buy(); self._step11_deviation_sell()
        self._step12_basic_signals(); self._step13_strength_scores()
        self._step14_strength_history(); self._step15_breakthrough()
        self._step16_stabilization(); self._step17_major_signals()
        self._step18_level_determination()
        return self.df

    def get_signals(self):
        df=self.compute(); signals=[]
        for _,row in df[df['TG_买入信号']].iterrows():
            signals.append({'date':str(row['Date'])[:10],'signal_type':'buy','signal_level':int(row['TG_买入层级']),
                'strength':round(float(row['TG_买入强度']),4),'tg_momentum':round(float(row['TG动量']),4),
                'close':float(row['Close']),'volume_ratio':round(float(row['量比']),2),
                'dist_from_low':round(float(row['距低点']),2),'kdj_j':round(float(row['J']),2)})
        return signals
