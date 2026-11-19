# 扩展校验器

## 内置校验

项目中使用的代理校验方法全部定义在 `helper/validator.py` 中，通过 `ProxyValidator` 类中提供的装饰器来区分。校验方法返回 `True` 表示校验通过，返回 `False` 表示校验不通过。

代理校验方法分为三类：

| 类型 | 装饰器 | 说明 |
|------|--------|------|
| `preValidator` | `@ProxyValidator.addPreValidator` | 预校验，在代理抓取后验证前调用 |
| `httpValidator` | `@ProxyValidator.addHttpValidator` | 代理可用性校验，通过则认为代理可用 |
| `httpsValidator` | `@ProxyValidator.addHttpsValidator` | 校验代理是否支持 HTTPS |

每种校验可以定义多个方法，只有**所有**方法都返回 `True` 的情况下才视为该校验通过。

### 校验执行顺序

```mermaid
graph LR
    A[抓取代理] --> B[preValidator]
    B -->|通过| C[httpValidator]
    B -->|失败| F[丢弃]
    C -->|通过| D[代理可用]
    C -->|失败| F
    D --> E[httpsValidator]
    E -->|通过| G[标记 HTTPS=True]
    E -->|失败| H[标记 HTTPS=False]
```

- `preValidator` 校验通过的代理才会进入可用性校验
- `httpValidator` 校验通过后认为代理可用，更新入代理池
- `httpsValidator` 校验通过后视为代理支持 HTTPS，更新代理的 `https` 属性为 `True`

## 扩展校验

在 `helper/validator.py` 中已有自定义校验的示例，自定义函数需返回 `True` 或者 `False`，使用 `ProxyValidator` 中提供的装饰器来区分校验类型。

### 示例 1：自定义代理可用性校验

```python
@ProxyValidator.addHttpValidator
def customValidatorExample01(proxy):
    """自定义代理可用性校验函数"""
    proxies = {"http": "http://{proxy}".format(proxy=proxy)}
    try:
        r = requests.get("http://www.baidu.com/", headers=HEADER, proxies=proxies, timeout=5)
        return True if r.status_code == 200 and len(r.content) > 200 else False
    except Exception as e:
        return False
```

### 示例 2：自定义 HTTPS 校验

```python
@ProxyValidator.addHttpsValidator
def customValidatorExample02(proxy):
    """自定义代理是否支持 HTTPS 校验函数"""
    proxies = {"https": "http://{proxy}".format(proxy=proxy)}
    try:
        r = requests.get("https://www.baidu.com/", headers=HEADER, proxies=proxies, timeout=5)
        return True if r.status_code == 200 and len(r.content) > 200 else False
    except Exception as e:
        return False
```

!!! note
    在运行代理可用性校验时，所有被 `ProxyValidator.addHttpValidator` 装饰的函数会依次按定义顺序执行，只有当所有函数都返回 `True` 时才会判断代理可用。`HttpsValidator` 运行机制也是如此。

## V2 扩展接口

以上装饰器和执行顺序适用于 legacy，以及 shadow 模式中的旧引擎。V2 不会隐式继承这些规则，也不会先要求 HTTP_URL 可达；主规则见 [代理检测 V2](../validation-v2.md)。

V2 的 policy.hooks 接受已安装模块的 module:function 列表。函数收到两个参数：不含用户名/密码的候选信息字典、已通过内置验证的结果摘要字典；必须返回 True 或 False。False 得到 custom_rejected；异常或非布尔值为 environment_error，不计硬连接失败。hook 只能继续收紧已通过结果，不能把 TLS、目标或协议失败改为成功。

模块需要随镜像构建或明确挂载后才可使用。hook 设计为纯结果检查，不自行发起网络请求；它在可回收 worker 中执行，仍受整轮截止和父进程退出守护限制。修改 hook 实现后应发布新版本或更新检测策略，不能假定旧结果已经通过新实现。
