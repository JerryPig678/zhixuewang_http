# zhixuewang-http

本项目实现了对智学网登录模块的逆向，不依赖于 Playwright 即可进行登录。本项目仅用于学生查询和监听成绩使用，希望官方不要修改登录逻辑（逆向不易！），或者开放官方查询 API。

Pure-HTTP login for zhixue.com (智学网) with Geetest v4 slide captcha bypass — no browser required.

## How It Works

The entire login flow is replicated via HTTP requests, eliminating the need for Playwright or any browser engine. This makes it suitable for headless server environments.

### Login Chain (8 Steps)

```
Step 0  Warm up      → GET zhixue.com (establish JSESSIONID cookie)
Step 1  Get SSO URLs  → GET /login/getServiceUrl → casUrl, serviceUrl
Step 2  Captcha #1    → Solve Geetest v4 slide captcha (pure HTTP + OpenCV)
Step 3  Pre-login     → POST /edition/login → userId, captchaId2
Step 4  Captcha #2    → Solve Geetest v4 slide captcha again (for SSO)
Step 5  SSO Auth      → GET {casUrl}/v1/getSingleAt → at_token, at_service
Step 6  SSO Redirect  → Manual 3-step redirect chain (the key trick):
  6a    GET atLogin   → CASTGC cookie
  6b    GET sso/login → service ticket (ST)
  6c    POST service  → final session cookies
Step 7  Complete      → POST /loginSuccess/
```

### The Key Trick: Bypassing Changyan SSO Without JS

The SSO gateway at `open.changyan.com` normally requires JavaScript execution to complete authentication. Most implementations fall back to Playwright at this point.

This project **manually follows the 3-step redirect chain** (6a → 6b → 6c), extracting intermediate credentials at each step and passing them to the next — no JS engine needed.

### Captcha Bypass

Uses [GeekedTest](https://github.com/AlejandroAk662/GeekedTest) — an open-source Geetest v4 captcha solver — to bypass slide captchas via:
- Pure HTTP captcha loading (no browser needed)
- OpenCV template matching for slide offset detection
- ddddocr for icon-type captcha OCR
- AES + RSA encryption for the `w` parameter (sign.py)
- Proof-of-work computation for `pow_detail`

> **Note**: GeekedTest supports slide, icon, and gobang captcha types. This project only uses the slide solver, as it is the most reliable. Non-slide types trigger a retry until a slide captcha appears.

The `geeked/` directory is a vendored copy of GeekedTest, slightly adapted for this project's import structure.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the demo (login + score query)
python demo.py -u YOUR_STUDENT_ID -p YOUR_PASSWORD

# Render the annotated answer sheet of a subject as JPEG
python demo.py --sheet 数学
python demo.py --sheet 数学 -e 2   # pick exam by index (1 = latest)
```

### Answer Sheet Rendering

`demo.py --sheet <subject>` downloads the answer sheet images and renders
webpage-style grading annotations onto them with PIL (no browser):

- Red total score bar
- Wrong-question red boxes + per-question score labels (`13题 14/17 -3`)
- Per-subquestion deduction grid (`16(2): -3/3`), from the `checksheet`
  API's `stepDatas[].stepStandardScore` (full scores) and
  `answerRecordDetails[].subTopics[].stepRecords` (scores)
- Teacher spot marks (`+1`) at their original positions

Implemented in [answer_sheet.py](answer_sheet.py); also used by the
[AstrBot QQ plugin](https://github.com/JerryPig678/astrbot_plugin_zhixuewang).

## File Structure

```
zhixuewang-http/
├── custom_provider.py   # Core: 8-step pure-HTTP login orchestrator
├── crypto.py            # RC4 + RSA R2/P password encryption
├── answer_sheet.py      # Answer sheet annotation rendering (PIL)
├── demo.py              # Demo: login + query scores + answer sheet rendering
├── geeked/              # Geetest v4 captcha solver (vendored)
│   ├── __init__.py
│   ├── geeked.py        # Main Geeked class
│   ├── sign.py          # w parameter generation (AES + RSA + PoW)
│   ├── slide.py         # Slide captcha solver (OpenCV template matching)
│   ├── icon.py          # Icon captcha solver (ddddocr)
│   ├── gobang.py        # Gobang captcha solver
│   ├── dddd_server.py   # ddddocr wrapper
│   └── models/          # ONNX model for icon classification
│       └── charsets.json
├── requirements.txt
└── README.md
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `curl_cffi` | HTTP client with browser TLS fingerprint impersonation |
| `pycryptodome` | RSA + AES encryption for password and w parameter |
| `opencv-python` | Slide captcha template matching |
| `ddddocr` | Icon captcha OCR |
| `numpy` | Image processing |
| `Pillow` | Answer sheet annotation rendering |
| `zhixuewang` | Score query API (optional, for demo.py only) |

## Credits

- [GeekedTest](https://github.com/AlejandroAk662/GeekedTest) — Geetest v4 captcha solver
- [zhixuewang](https://github.com/anwenhu/zhixuewang) — zhixue.com API wrapper

## Author

JerryPig678

## Disclaimer / 免责声明

本项目仅供学习与技术交流，仅对智学网登录流程进行逆向工程研究。

- 本项目**不提供**任何账号信息，使用者需使用自己的合法账号。
- 本项目**不用于**任何商业用途，**不用于**批量爬取、刷分、作弊等违规行为。
- 使用本项目所产生的一切后果由使用者自行承担，作者**不承担任何责任**。
- 如果本项目侵犯了您的合法权益，请联系作者删除。

This project is for educational and technical research purposes only. The author is not responsible for any misuse or damage caused by this project. If this project infringes on your legal rights, please contact the author for removal.

## License

MIT
