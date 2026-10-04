# 智学网「手阅作业」接口文档

> 逆向来源：第三方轻量 App「智伴」(com.zhixue.lite) + 实测验证
> 客户端实现：[app_api.py](app_api.py)
> 分支：`feature/shouyue-homework`

## 一、登录认证

### 1.1 可行链路（实测通过）✅

网页版界面**没有**手阅作业入口，但 zxbReport 接口对普通学生 web 会话完全开放。
不要走 App 端 casLogin（见 1.2），直接复用 web 登录：

```
web 登录（zhixuewang_http 的 http_login，CAS 全流程含验证码）
    ↓ 拿到 cookies
GET /container/app/token/getToken     （auth 头，见 1.3）
    ↓ 拿到 XToken (JWT)
调用 zxbReport 系列接口
```

### 1.2 App 端链路（死胡同）❌

智伴 App 的登录链路逆向结果是：

```
POST https://open.changyan.com/sso/v1/api     ← 这一步能成功
     form: username, password(R2/P), mac=0, key=auto, encode=true,
           encodeType=R2/P, client=android, appId=zhixue_parent,
           method=sso.login.account, extInfo={"deviceId":"0"}
     返回: tgt / at / userId / loginName / hashLoginName
     密码编码: 明文先 StringBuilder.reverse() 反转，
              再标准 RSA/ECB/PKCS1Padding（公钥与 web 端相同），hex 输出

POST https://www.zhixue.com/container/app/login/casLogin
     form: at + userId      ← 逆向确认只有这两个 @Field
     返回: errorCode 50011 "账号或密码错误"   ← 永远失败
```

**casLogin 现在是死接口**：参数与 App 字节码完全一致（at+userId、无其他
@Field、无拦截器加头），UA/cookie 矩阵测试均 50011。推测服务端已下线对
changyan 签发 at 的校验。

### 1.3 auth 请求头算法（zxbReport 必须带）

```python
import hashlib, time, uuid

def auth_headers(xtoken: str | None = None) -> dict:
    guid = str(uuid.uuid4())
    ts = str(int(time.time() * 1000))
    tok = hashlib.md5((guid + ts + "iflytek!@#123student").encode()).hexdigest()
    h = {
        "authbizcode": "0001",
        "authguid": guid,
        "authtimestamp": ts,
        "authtoken": tok,
    }
    if xtoken:
        h["XToken"] = xtoken
    return h
```

- `authbizcode=0001` 为学生端业务码（教师端不同）
- 盐值 `iflytek!@#123student` 与 zhixuewang 开源库一致
- getToken 返回的 XToken 是 JWT，有效期较长（过期重新 GET getToken 即可）

### 1.4 ⚠️ 关键注意事项：token 必须放请求头，不是 form

**错误用法**（返回 200 + 空 body，响应头 `tokenstatus: timeout`）：

```python
s.post(url, data={"token": xtoken, ...})   # ❌ 空响应，无任何报错
```

**正确用法**：

```python
s.post(url, data={...业务参数...}, headers=auth_headers(xtoken))  # ✅
```

放 form 里不会报错，只会静默返回空 body，非常难排查。

---

## 二、手阅作业接口

统一 base URL：`https://www.zhixue.com`
统一格式：`POST`，`application/x-www-form-urlencoded`，响应为
`{"errorCode": 0, "errorInfo": "操作成功", "result": {...}}`（errorCode 非 0 即失败）

### 2.1 作业列表

```
POST /zxbReport/report/getPageAllExamList
form: reportType=homework
      pageIndex=1
      pageSize=10
      actualPosition=0
headers: auth_headers(xtoken)
```

`reportType` 取值：
- `homework` — 作业/手阅作业列表
- `exam` — 考试列表

`result.examInfoList[]` 关键字段：

| 字段 | 说明 |
|---|---|
| examId | 作业 ID，后续接口都要用 |
| examName | 作业名 |
| examType | `homework` |
| score | 得分（列表里可能为 0，以 getCheckSheet 为准） |
| examCreateDateTime | 创建时间戳（毫秒） |
| hasNextPage | 是否还有下一页（在 result.pagination 同级） |

### 2.2 作业报告主页（拿 paperId）

```
POST /zxbReport/report/exam/getReportMain
form: examId=<examId>
      token=<xtoken>          ← 这个接口 token 放 form（与上面不同！）
headers: auth_headers(xtoken)
```

`result.paperList[]` 关键字段：

| 字段 | 说明 |
|---|---|
| paperId | 试卷 ID，getCheckSheet 要用 |
| paperName | 试卷名 |
| subjectName / subjectCode | 科目 |
| userScore / standardScore | 得分 / 满分 |
| scoringModel | 计分模式 |

> 注意：zxbReport 各接口 token 位置不统一——列表接口不需要 token，
> getReportMain/getCheckSheet 的 token 放 form。稳妥做法是 form 和
> header 都带上。

### 2.3 答题卡（核心接口）

```
POST /zxbReport/report/paper/getCheckSheet
form: examId=<examId>
      paperId=<paperId>
      token=<xtoken>
headers: auth_headers(xtoken)
```

`result` 关键字段：

| 字段 | 说明 |
|---|---|
| sheetImages | JSON 字符串，`json.loads` 后是已批改答题卡图片 URL 列表（OSS 签名 URL，**有过期时间**，约几小时，要及时下载） |
| sheetDatas | JSON 字符串，答题卡坐标数据（见 2.4） |
| score / standardScore | 总分 / 满分 |
| stepDatas | 分小问批改数据，**手阅作业通常为空数组**（语文/英语无分步数据同理） |
| markingTopicDetail | 题号映射（JSON 字符串） |
| cutBlockDetail | 切块详情（JSON 字符串） |
| showSingleTopicScore | 是否显示单题得分 |

图片 URL 形如：
`https://zhixue-sc.oss-cn-hangzhou.aliyuncs.com/scDV2dv_marking/scanFile/.../AfterCorrect_xxx.jpg?Expires=...&OSSAccessKeyId=...&Signature=...`
直接 GET 下载即可（无需额外认证），下载后为 `AfterCorrect`（已批改）原图。

### 2.4 sheetDatas 坐标格式

`json.loads(sheetDatas)` 后结构：

```
answerSheetLocationDTO
├── answerSheetFrom: "TranscriptsPoints"
└── pageSheets[]                     ← 每页一个
    ├── pageIndex: 0
    ├── locatePoint[]                ← 定位点（4 个角）
    └── sections[]
        ├── position {left, top, width, height}
        └── branch[]                 ← 客观题小块
            ├── ixList / numList     ← 题号
            ├── chooses              ← 选项
            └── firstOption          ← 首个选项坐标
```

坐标单位是**毫米级**（与考试答题卡一致），换算像素用 7.874 px/mm
（A3 横向 420mm）。注意 branch 坐标存在两种格式：相对 section 或
绝对页面坐标，需自动判别（相对解释出边界则按绝对处理）。

### 2.5 其他已知接口（同一 Retrofit 类 r4/c）

| 方法 | 路径 | 参数 |
|---|---|---|
| GET | /zxbReport/report/getPaperAnalysis | query: paperId, token |
| POST | /zxbReport/report/exam/getSubjectDiagnosis | examId, token |
| POST | /zxbReport/report/paper/getLevelTrend | examId, paperId, token |
| POST | /zxbReport/base/common/getUserInfo | token |
| POST | /container/app/modifyOriginPWD | loginName, originPWD, newPWD, token |

---

## 三、踩坑记录

1. **token 放 form 无效**：列表接口 token 放 form 得到空 body +
   `tokenstatus: timeout` 响应头，必须放请求头（见 1.4）。
2. **casLogin 50011**：App 端换 token 接口已死，别再尝试（见 1.2）。
3. **响应类型不统一**：changyan SSO 返回 JSP 包裹的 `('{...}')` +
   反斜杠续行，需要清洗后才能 `json.loads`；zxbReport 返回标准 JSON。
4. **手阅作业 stepDatas 为空**：渲染答题卡批注时不能假设分小问数据存在，
   只显示题级得分/总分。
5. **OSS 图片 URL 有时效**：Expires 参数约几小时后过期，拿到后立即下载。
6. **页面图片顺序**：sheetImages 数组顺序即页码顺序，文件名尾缀
   A.jpg/B.jpg 也对应页序。

## 四、快速使用

```bash
python app_api.py <账号> <密码>
# 输出：作业列表 → 第一场作业的报告 → 下载答题卡图片到 data/answer_sheets/
```

```python
from app_api import ZhixueAppClient

c = ZhixueAppClient()
c.login("账号", "密码")
exams = c.get_homework_list(page_size=10)
main = c.get_report_main(exams[0]["examId"])
paper = main["paperList"][0]
sheet = c.get_check_sheet(exams[0]["examId"], paper["paperId"])
paths = c.download_sheet_images(sheet, "data/answer_sheets")
```
