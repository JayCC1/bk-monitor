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

import { type PropType, defineComponent, onMounted } from 'vue';
import { shallowRef } from 'vue';

import { createAutoTimeRange } from './aiops-charts';
import MonitorCharts from './monitor-charts';
import { useSeriesFormatter } from '../hooks/use-series-formatter';
import { DEFAULT_TIME_RANGE } from '@/components/time-range/utils';
import { PanelModel } from '@/plugins/typings';

import type { AlarmDetail } from '@/pages/alarm-center/typings';

import './outlier-detection-chart.scss';

export default defineComponent({
  name: 'OutlierDetectionChart',
  props: {
    detail: {
      type: Object as PropType<AlarmDetail>,
      default: () => ({}),
    },
  },
  setup(props) {
    const { formatOutlierChartData } = useSeriesFormatter();
    const panel = shallowRef(null);
    const showRestore = shallowRef(false);
    const timeRange = shallowRef(DEFAULT_TIME_RANGE);
    const cacheTimeRange = shallowRef(DEFAULT_TIME_RANGE);
    const handleDataZoomChange = (value: any[]) => {
      if (JSON.stringify(timeRange.value) !== JSON.stringify(value)) {
        cacheTimeRange.value = JSON.parse(JSON.stringify(timeRange.value));
        timeRange.value = value;
        showRestore.value = true;
      }
    };

    const handleRestore = () => {
      const cacheTime = JSON.parse(JSON.stringify(cacheTimeRange.value));
      timeRange.value = cacheTime;
      showRestore.value = false;
    };

    onMounted(() => {
      initPanel();
    });

    const initPanel = async () => {
      const { startTime, endTime } = createAutoTimeRange(
        props.detail.begin_time,
        props.detail.end_time,
        props.detail.extra_info?.strategy?.items?.[0]?.query_configs?.[0]?.agg_interval
      );
      timeRange.value = [startTime, endTime];
      const panelSrcData = props.detail.graph_panel;
      const { id, title, subTitle, targets } = panelSrcData;
      const panelData = {
        id,
        title,
        subTitle,
        type: 'time-series-outlier',
        options: {},
        targets: targets.map(item => ({
          ...item,
          alias: '',
          options: {},
          data: {
            ...item.data,
            id: props.detail.id,
            function: undefined,
          },
          api: 'alert_v2.alertGraphQuery',
        })),
      };
      panel.value = new PanelModel(panelData);
    };

    /**
     * @description: 格式化图表数据
     * @param {any} data 图表接口返回的series数据
     */
    const formatterData = (data: any) => {
      const { graph_panel } = props.detail;
      const [{ alias }] = graph_panel.targets;
      return formatOutlierChartData(data, alias);
    };
    return {
      panel,
      showRestore,
      handleDataZoomChange,
      handleRestore,
      formatterData,
    };
  },
  render() {
    return (
      <div class='outlier-detection-chart'>
        {this.panel && (
          <MonitorCharts
            formatterData={this.formatterData}
            panel={this.panel}
            showRestore={this.showRestore}
            onDataZoomChange={this.handleDataZoomChange}
            onRestore={this.handleRestore}
          />
        )}
      </div>
    );
  },
});
