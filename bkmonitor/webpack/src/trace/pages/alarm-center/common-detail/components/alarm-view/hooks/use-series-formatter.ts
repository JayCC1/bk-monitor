/*
 * Tencent is pleased to support the open source community by making
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) available.
 *
 * Copyright (C) 2017-2025 Tencent.  All rights reserved.
 *
 * 蓝鲸智云PaaS平台 (BlueKing PaaS) is licensed under the MIT License.
 *
 * License for 蓝鲸智云PaaS平台 (BlueKing PaaS):
 *
 * ---------------------------------------------------
 * Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
 * documentation files (the "Software"), to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
 * to permit persons to whom the Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all copies or substantial portions of
 * the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
 * THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
 * IN THE SOFTWARE.
 */

import dayjs from 'dayjs';
import { useI18n } from 'vue-i18n';

import type { AlarmDetail } from '@/pages/alarm-center/typings';

/** 图表颜色常量 */
export const CHART_COLORS = {
  ANOMALY: '#E71818',
  FATAL_ALARM: '#e64545',
  FATAL_PERIOD: '#F8B4B4',
  FATAL_PERIOD_BG: 'rgba(234, 54, 54, 0.12)',
  TRIGGER_PHASE: '#DCDEE5',
  TRIGGER_PHASE_BG: 'rgba(155, 168, 194, 0.12)',
} as const;

/** 原始数据点类型 [value, timestamp] */
export type DataPoint = [null | number, number];

/** 接口返回的系列数据项 */
export interface ApiSeriesItem {
  alias?: string;
  datapoints: DataPoint[];
  dimensions?: Record<string, any>;
  stack?: string;
  target: string;
  time_offset?: string;
  type?: string;
  unit?: string;
  z?: number;
  [key: string]: any;
}

/** 图表系列数据项 */
export interface ChartSeriesItem {
  alias?: string;
  color?: string;
  data?: any[];
  datapoints?: DataPoint[];
  itemStyle?: Record<string, any>;
  lineStyle?: Record<string, any>;
  markArea?: Record<string, any>;
  markPoint?: Record<string, any>;
  name?: string;
  showSymbol?: boolean;
  symbolSize?: number;
  tooltip?: Record<string, any>;
  type?: string;
  yAxisIndex?: number;
  z?: number;
  [key: string]: any;
}

/** 格式化后的图表数据 */
export interface FormattedChartData {
  metrics?: any[];
  query_config?: any;
  series: ChartSeriesItem[];
}

/**
 * @function useSeriesFormatter 图表数据格式化 hooks
 * @description 将图表数据格式化为图表需要的数据格式 相关逻辑工具
 */
export const useSeriesFormatter = () => {
  const { t } = useI18n();

  /**
   * @description 格式化系列别名，替换占位变量
   * @param {string} name 原始系列名称(可能存在占位变量，如：$time_offset)
   * @param {Record<string, any>} compareData 替换数据源
   * @returns 格式化后的系列名称
   */
  const formatSeriesAlias = (name: string, compareData: Record<string, any> = {}): string => {
    if (!name) return name;
    let alias = name;

    for (const [key, val] of Object.entries(compareData)) {
      if (!val) continue;

      if (key === 'time_offset' && alias.includes('$time_offset')) {
        const timeMatch = val.match(/(-?\d+)(\w+)/);
        const replacement =
          timeMatch?.length > 2
            ? dayjs.tz().add(-timeMatch[1], timeMatch[2]).fromNow().replace(/\s*/g, '')
            : val.replace('current', t('当前'));
        alias = alias.replaceAll('$time_offset', replacement);
      } else if (typeof val === 'object') {
        for (const valKey of Object.keys(val).sort((a, b) => b.length - a.length)) {
          alias = alias.replaceAll(`$${key}_${valKey}`, val[valKey]);
        }
      } else {
        alias = alias.replaceAll(`$${key}`, val);
      }
    }

    return alias.replace(/(\|\s*)+\|/g, '|').replace(/\|$/g, '');
  };

  /**
   * @description 将接口返回的 datapoints 格式转换为图表需要的 data 格式
   * @param {ApiSeriesItem[]} data 接口返回的系列数据
   * @returns 转换后的图表系列数据
   */
  const transformSeries = (data: ApiSeriesItem[] | { series: ApiSeriesItem[] }): { series: ChartSeriesItem[] } => {
    if (!data) return { series: [] };

    // 如果已经是 { series: [...] } 格式，直接返回
    if ('series' in data && Array.isArray(data.series)) {
      return data as { series: ChartSeriesItem[] };
    }

    const seriesArray = data as ApiSeriesItem[];
    const mapData: Record<string, number> = {};

    return {
      series: seriesArray.map(({ datapoints, target, ...item }) => {
        // 处理重名系列，添加数字后缀
        mapData[target] = mapData[target] !== undefined ? mapData[target] + 1 : 0;

        return {
          ...item,
          data: datapoints?.map(set => (Array.isArray(set) ? set.slice().reverse() : [])) || [],
          name: mapData[target] === 0 ? target : `${target}${mapData[target]}`,
          showSymbol: false, // 默认不显示点，只有hover时候显示该点
          symbolSize: 6,
        };
      }),
    };
  };

  /**
   * @description 创建标记区域配置
   * @param {string} startTime 开始时间戳字符串
   * @param {string} endTime 结束时间戳字符串
   * @param {string} color 区域颜色
   * @returns 标记区域配置对象
   */
  const createMarkArea = (startTime: string, endTime: string, color: string) => ({
    data: [[{ xAxis: startTime }, { xAxis: endTime }]],
    itemStyle: { color },
    silent: true,
  });

  /**
   * @description 格式化告警图表数据，添加异常点、告警标记等辅助系列
   * @param {any} data 原始图表数据
   * @param {AlarmDetail} detail 告警详情
   * @param {string} alias 系列别名模板
   * @returns 包含异常标记、告警阶段等辅助系列的完整图表数据
   */
  const formatAlarmChartData = (
    data: { metrics?: any[]; query_config?: any; series: ApiSeriesItem[] },
    detail: AlarmDetail,
    alias?: string
  ): FormattedChartData => {
    if (!data?.series?.length) return data as FormattedChartData;

    const series: ChartSeriesItem[] = data.series.map(s => ({
      ...s,
      alias: alias ? formatSeriesAlias(alias, { ...s, tag: s.dimensions }) || s.alias : s.alias,
    }));

    const datapoints = series[0]?.datapoints;
    if (!datapoints?.length) return { ...data, series };

    const max = datapoints.reduce((prev, cur) => Math.max(prev, cur[0] ?? 0), 0);
    const emptyDatapoints: DataPoint[] = datapoints.map(item => [null, item[1]]);
    const beginTimeStr = String(detail.begin_time * 1000);
    const firstAnomalyTimeStr = String(detail.first_anomaly_time * 1000);
    const endTimeStr = String(detail.end_time ? detail.end_time * 1000 : datapoints[datapoints.length - 1][1]);

    return {
      ...data,
      series: [
        ...series,
        // 异常点散点图
        {
          alias: t('异常'),
          color: CHART_COLORS.ANOMALY,
          datapoints: datapoints.map(item => {
            const isAnomaly = detail.anomaly_timestamps?.includes(Number(String(item[1]).slice(0, -3)));
            return [isAnomaly ? item[0] : null, item[1]] as DataPoint;
          }),
          itemStyle: { color: CHART_COLORS.ANOMALY },
          symbolSize: 5,
          tooltip: { show: false },
          type: 'scatter',
        },
        // 致命告警产生标记点
        {
          alias: t('致命告警产生'),
          color: CHART_COLORS.FATAL_ALARM,
          datapoints,
          lineStyle: { opacity: 0 },
          markPoint: {
            data: [{ coord: [beginTimeStr, max === 0 ? 1 : max] }],
            label: {
              color: CHART_COLORS.FATAL_ALARM,
              fontFamily: 'icon-monitor',
              fontSize: 18,
              formatter: '\ue606',
              position: 'top',
              show: true,
            },
            symbol: 'circle',
            symbolSize: 0,
          },
          tooltip: { show: false },
          type: 'line',
          yAxisIndex: 1,
        },
        // 告警触发阶段区域
        {
          alias: t('告警触发阶段'),
          color: CHART_COLORS.TRIGGER_PHASE,
          datapoints: emptyDatapoints,
          markArea: createMarkArea(firstAnomalyTimeStr, beginTimeStr, CHART_COLORS.TRIGGER_PHASE_BG),
          tooltip: { show: false },
          type: 'line',
        },
        // 致命告警时段区域
        {
          alias: t('致命告警时段'),
          color: CHART_COLORS.FATAL_PERIOD,
          datapoints: emptyDatapoints,
          markArea: createMarkArea(beginTimeStr, endTimeStr, CHART_COLORS.FATAL_PERIOD_BG),
          tooltip: { show: false },
          type: 'line',
          z: 1,
        },
      ],
    };
  };

  /**
   * @description 格式化离群检测图表数据
   * @param {any} data 原始图表数据
   * @param {string} alias 系列别名模板
   * @returns 格式化后的图表数据
   */
  const formatOutlierChartData = (
    data: { metrics?: any[]; query_config?: any; series: ApiSeriesItem[] },
    alias?: string
  ): FormattedChartData => {
    if (!data?.series?.length) return data as FormattedChartData;

    return {
      ...data,
      series: data.series.map(item => ({
        ...item,
        alias: alias ? formatSeriesAlias(alias, { ...item, tag: item.dimensions }) || item.alias : item.alias,
      })),
    };
  };

  /**
   * @description 检查系列数据是否有效（非空）
   * @param {any} data 系列数据
   * @returns 是否有有效数据
   */
  const hasValidSeries = (data: any): boolean => {
    if (!data) return false;

    if (Array.isArray(data)) {
      return data.length > 0 && data.some(item => item?.datapoints?.length);
    }

    if ('series' in data) {
      return data.series?.length > 0;
    }

    return false;
  };

  /**
   * @description 获取系列数据的最大最小值
   * @param {DataPoint[]} datapoints 数据点数组
   * @returns 最大最小值对象
   */
  const getSeriesExtremum = (datapoints: DataPoint[]): { max: number; min: number } => {
    if (!datapoints?.length) return { max: 0, min: 0 };

    let max = -Infinity;
    let min = Infinity;

    for (const point of datapoints) {
      const value = point[0];
      if (value !== null && value !== undefined) {
        if (value > max) max = value;
        if (value < min) min = value;
      }
    }

    return {
      max: max === -Infinity ? 0 : max,
      min: min === Infinity ? 0 : min,
    };
  };

  /**
   * @description 合并多个系列的时间轴数据
   * @param {ApiSeriesItem[]} seriesList 系列数据列表
   * @returns 合并后的时间戳数组（已排序去重）
   */
  const mergeTimeAxis = (seriesList: ApiSeriesItem[]): number[] => {
    const timeSet = new Set<number>();

    for (const series of seriesList) {
      for (const point of series.datapoints || []) {
        timeSet.add(point[1]);
      }
    }

    return Array.from(timeSet).sort((a, b) => a - b);
  };

  return {
    CHART_COLORS,
    createMarkArea,
    formatAlarmChartData,
    formatOutlierChartData,
    formatSeriesAlias,
    getSeriesExtremum,
    hasValidSeries,
    mergeTimeAxis,
    transformSeries,
  };
};

export default useSeriesFormatter;
