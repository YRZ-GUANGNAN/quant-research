"""quant —— 本地 A 股量化研究系统。

模块划分：
- quant.data     原始行情与指数成分数据采集、本地缓存
- quant.engine   模拟盘撮合、回测循环、绩效指标
- quant.factors  因子计算与截面标准化
- quant.models   因子合成与机器学习模型
- quant.strategies 选股 / 调仓策略
"""

__version__ = "0.1.0"
