## process-service.ts

### getHostProcessList

获取选中主机的进程列表。 **数据不太够，需要安装设计稿中补充字段**

| 项               | 值                                                             |
| ---------------- | -------------------------------------------------------------- |
| **Service 签名** | `(params: GetHostProcessListParams) => Promise<ProcessItem[]>` |
| **HTTP**         | `POST /rest/v2/scene_view/get_host_process_list/`              |

**请求参数**：

```typescript
interface GetHostProcessListParams {
  bk_target_cloud_id?: string;
  bk_target_ip?: string;
  start_time: number;
  end_time: number;
}
```

| 参数                 | 类型     | 必填                         | 说明                    |
| -------------------- | -------- | ---------------------------- | ----------------------- |
| `bk_target_ip`       | `string` | 与 `bk_target_cloud_id` 配套 | 目标主机 IP             |
| `bk_target_cloud_id` | `string` | 与 `bk_target_ip` 配套       | 云区域 ID               |
| `start_time`         | `number` | 是                           | 时间范围起始（Unix 秒） |
| `end_time`           | `number` | 是                           | 时间范围结束（Unix 秒） |

> 底层接口还支持 `bk_biz_id`、`bk_host_id`，接入时由 Service 层补充。

#### 返回数据类型

```typescript
enum EProcessPortStatus {
  Normal = 0,
  Abnormal = 1,
}

interface ProcessItem {
  bindIp: string;
  cpuUsage: number; // CPU 使用率
  fdNum: number; // 文件句柄使用数量
  fdUsageRate: string; // 文件句柄使用率
  hostIp: string;
  id: string;
  instanceCount: number; // 进程实例数量
  memRss: number; // 物理内存使用量，单位字节
  memUsage: number; // 内存使用率
  name: string;
  port: number;
  portStatus: EProcessPortStatus; // 端口状态，0 为正常，1 为异常
  protocol: string;
  startCommand: string;
  status: number; // 进程状态（原有字段）
  uptime: number; // 运行时长范围，单位毫秒
  user: string;
}

// Service 返回类型
type GetHostProcessListResult = ProcessItem[];
```

#### 返回示例

```json
[
  {
    "id": "328392",
    "name": "bash",
    // "pid": 10086,  字段已删除
    "protocol": "TCP",
    "bindIp": "0.0.0.0",
    "port": 18000,
    "portStatus": 1,
    "user": "root",
    "hostIp": "123.234.34.34",
    "cpuUsage": 19,
    "memRss": 96468992,
    "memUsage": 23,
    "uptime": 23040,
    "startCommand": "agent run p/opt/datadog-agent/run/agent.pid",
    "status": 1,
    "fdNum": 10,
    "fdUsageRate": "2",
    "instanceCount": 1
  },
  {
    "id": "94854",
    "name": "mysqld",
    // "pid": 10088,  字段已删除
    "protocol": "TCP",
    "bindIp": "0.0.0.0",
    "port": 3306,
    "portStatus": 0, // 端口状态,0为正常，1 为异常
    "user": "user01",
    "hostIp": "43.84.75.498",
    "cpuUsage": 12, //CPU使用率
    "memRss": 134217728, // 物理内存是使用量，单位字节
    "memUsage": 35, // 内存使用率
    "uptime": 8648980, // 运行时长范围单位毫秒
    "startCommand": "/usr/sbin/mysqld --defaults-file=/etc/my.cnf",
    "status": 1, // 进程状态 （该字段为原有字段）
    "fdNum": 10, // 文件句柄使用数量
    "fdUsageRate": "2", // 文件句柄使用率
    "instanceCount": 1 // 进程实例数量
  }
]
```
