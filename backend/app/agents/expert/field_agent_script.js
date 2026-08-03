/**
 * WBS-13: PlaywrightFieldAgent 只读体验脚本模板
 *
 * 通过 browserless /function API 执行。
 * 硬编码只读约束：不填表、不登录、不提交、不下载。
 *
 * 占位符（由 PlaywrightFieldAgent._build_script() 替换）：
 *   {{TARGET_URL_JSON}}        — 目标网站 URL（已 json.dumps，JS 合法字面量）
 *   {{COMPANY_NAME_JSON}}      — 目标公司名（已 json.dumps）
 *   {{TASK_DESCRIPTION_JSON}}  — 任务描述（已 json.dumps）
 *   {{MAX_PAGES}}              — 最大浏览页数
 *   {{MAX_CLICKS}}             — 最大点击次数
 *   {{SCREENSHOT_ENABLED}}     — true/false 是否截图
 *   {{TIMEOUT_MS}}             — 导航超时（毫秒）
 */
async ({ page, context }) => {
  // ── 常量 ──────────────────────────────────────────────────────────
  const TARGET_URL = {{TARGET_URL_JSON}};
  const COMPANY_NAME = {{COMPANY_NAME_JSON}};
  const MAX_PAGES = {{MAX_PAGES}};
  const MAX_CLICKS = {{MAX_CLICKS}};
  const SCREENSHOT_ENABLED = {{SCREENSHOT_ENABLED}};
  const TIMEOUT_MS = {{TIMEOUT_MS}};
  const MAX_TEXT_LEN = 15000;

  // ── 结果容器 ──────────────────────────────────────────────────────
  const result = {
    pages: [],
    clickPath: [],
    status: 'OK',
    error: '',
  };

  let stepCounter = 0;
  let clickCount = 0;
  const visitedUrls = new Set();
  const delay = milliseconds => new Promise(
    resolve => setTimeout(resolve, milliseconds)
  );

  // ── 辅助函数 ──────────────────────────────────────────────────────

  /** 记录操作步骤 */
  function recordStep(action, url, selector, elementText) {
    result.clickPath.push({
      step: stepCounter++,
      action: action,
      url: url || page.url(),
      selector: selector || '',
      elementText: (elementText || '').substring(0, 200),
      timestamp: new Date().toISOString(),
    });
  }

  /** 检查元素是否是安全的导航链接（非登录/提交/下载） */
  function isSafeNavLink(href, text) {
    const lower = (href + text).toLowerCase();
    const blocked = [
      'login', 'signin', 'logout', 'register', 'signup',
      'submit', 'post', 'comment', 'reply',
      'download', '.pdf', '.zip', '.exe', '.doc',
      'payment', 'checkout', 'cart', 'order',
      'admin', 'wp-admin', 'dashboard',
      'delete', 'remove', 'unsubscribe',
      'javascript:void', 'javascript:;', 'mailto:', 'tel:',
    ];
    for (const pattern of blocked) {
      if (lower.includes(pattern)) return false;
    }
    // 必须是 http/https 或相对路径
    if (href.startsWith('javascript:')) return false;
    if (href.startsWith('mailto:')) return false;
    if (href.startsWith('tel:')) return false;
    return true;
  }

  /** 提取页面文本内容 */
  async function extractPageText() {
    try {
      const text = await page.evaluate(() => {
        // 移除不可见元素和噪声元素
        const noiseSelectors = [
          'script', 'style', 'noscript', 'iframe',
          '[aria-hidden="true"]',
        ];
        const clone = document.body.cloneNode(true);
        noiseSelectors.forEach(sel => {
          clone.querySelectorAll(sel).forEach(el => el.remove());
        });
        return (clone.innerText || clone.textContent || '').replace(/\s+/g, ' ').trim();
      });
      return text.substring(0, MAX_TEXT_LEN);
    } catch (e) {
      return '[文本提取失败: ' + e.message + ']';
    }
  }

  /** 提取导航链接 */
  async function extractNavLinks() {
    try {
      const links = await page.evaluate(() => {
        const results = [];
        const seen = new Set();

        // 优先从 nav 元素获取
        const navContainers = document.querySelectorAll('nav, [class*="nav"], [id*="nav"], header');
        const containers = navContainers.length > 0 ? navContainers : [document.body];

        containers.forEach(container => {
          const anchors = container.querySelectorAll('a[href]');
          anchors.forEach(a => {
            const text = (a.innerText || a.textContent || '').trim();
            const href = a.getAttribute('href') || '';
            if (!text || text.length > 100) return;
            const key = `${text}|${href}`;
            if (seen.has(key)) return;
            seen.add(key);
            results.push({ text: text.substring(0, 80), href: href });
          });
        });

        return results.slice(0, 30);
      });

      // 过滤不安全链接
      return links.filter(l => isSafeNavLink(l.href, l.text));
    } catch (e) {
      return [];
    }
  }

  /** 解析相对 URL 为绝对 URL */
  function resolveUrl(href) {
    try {
      return new URL(href, page.url()).href;
    } catch (e) {
      return '';
    }
  }

  /** 遇到验证码、登录墙或要求输入敏感信息时立即停止，不尝试绕过。 */
  async function restrictedBarrier() {
    return await page.evaluate(() => {
      const text = (document.body?.innerText || '').toLowerCase();
      const patterns = [
        '验证码', '滑动验证', '安全验证', '人机验证',
        'captcha', 'verify you are human', 'access denied',
        '请先登录', '登录后访问', 'sign in to continue',
      ];
      if (patterns.some(pattern => text.includes(pattern))) return true;
      return Boolean(document.querySelector(
        'input[type="password"], input[name*="captcha" i], input[placeholder*="验证码"]'
      ));
    });
  }

  // ── 主流程 ────────────────────────────────────────────────────────

  try {
    // 1. 导航到目标 URL
    recordStep('navigate', TARGET_URL, '', '导航到目标网站');
    await page.goto(TARGET_URL, {
      waitUntil: 'networkidle2',
      timeout: TIMEOUT_MS,
    });
    // SPA 额外等待
    await delay(2000);
    if (await restrictedBarrier()) {
      result.status = 'BLOCKED';
      result.error = '检测到验证码、登录墙或敏感信息输入要求，已按合规策略停止';
      recordStep('blocked', page.url(), '', result.error);
      return result;
    }

    // 2. 提取首页信息
    const homeTitle = await page.title();
    const homeText = await extractPageText();
    const homeNavLinks = await extractNavLinks();
    visitedUrls.add(page.url());

    // 3. 首页截图
    let homeScreenshot = '';
    if (SCREENSHOT_ENABLED) {
      try {
        const screenshotBuf = await page.screenshot({ type: 'png', fullPage: false });
        homeScreenshot = screenshotBuf.toString('base64');
        recordStep('screenshot', page.url(), '', '首页截图');
      } catch (e) {
        result.error += '首页截图失败: ' + e.message + '; ';
      }
    }

    result.pages.push({
      url: page.url(),
      title: homeTitle,
      textContent: homeText,
      screenshotBase64: homeScreenshot,
      navLinks: homeNavLinks.slice(0, 15),
      capturedAt: new Date().toISOString(),
    });

    // 4. 点击导航链接（限制 max_pages）
    const safeLinks = homeNavLinks.filter(l => {
      const absUrl = resolveUrl(l.href);
      return absUrl && !visitedUrls.has(absUrl) && absUrl.startsWith('http');
    });

    for (const link of safeLinks) {
      if (result.pages.length >= MAX_PAGES) break;
      if (clickCount >= MAX_CLICKS) break;

      const absUrl = resolveUrl(link.href);
      if (!absUrl || visitedUrls.has(absUrl)) continue;

      try {
        // 点击前记录
        recordStep('click', absUrl, `a[href="${link.href}"]`, link.text);
        clickCount++;

        // 尝试点击
        const linkEl = await page.$(`a[href="${link.href}"]`);
        if (!linkEl) continue;

        const navigation = page.waitForNavigation({
          waitUntil: 'networkidle2',
          timeout: 15000,
        }).catch(() => null);
        await linkEl.click();
        await navigation;
        await delay(1500);
        if (await restrictedBarrier()) {
          result.status = 'BLOCKED';
          result.error = '导航后检测到验证码、登录墙或敏感信息输入要求，已停止';
          recordStep('blocked', page.url(), '', result.error);
          break;
        }

        const currentUrl = page.url();
        // 避免重复访问
        if (visitedUrls.has(currentUrl)) continue;
        visitedUrls.add(currentUrl);

        // 提取新页面
        const pageTitle = await page.title();
        const pageText = await extractPageText();
        const pageNavLinks = await extractNavLinks();

        // 截图
        let pageScreenshot = '';
        if (SCREENSHOT_ENABLED) {
          try {
            const buf = await page.screenshot({ type: 'png', fullPage: false });
            pageScreenshot = buf.toString('base64');
            recordStep('screenshot', currentUrl, '', `页面截图: ${pageTitle}`);
          } catch (e) {
            // 截图失败不中断流程
          }
        }

        result.pages.push({
          url: currentUrl,
          title: pageTitle,
          textContent: pageText,
          screenshotBase64: pageScreenshot,
          navLinks: pageNavLinks.slice(0, 10),
          capturedAt: new Date().toISOString(),
        });
      } catch (e) {
        // 单页失败不中断全局
        recordStep('error', absUrl, `a[href="${link.href}"]`, '点击失败: ' + e.message);
      }
    }

    // 5. 生成摘要
    const pageTitles = result.pages.map(p => p.title).filter(Boolean);
    result.summary = `成功浏览 ${COMPANY_NAME || '目标'}网站，访问 ${result.pages.length} 个页面: ${pageTitles.join('、') || '(无标题)'}`;
    result.status = result.pages.length > 0 ? 'OK' : 'EMPTY';

  } catch (e) {
    result.status = 'ERROR';
    result.error = (result.error || '') + '脚本执行失败: ' + e.message;
    recordStep('error', TARGET_URL, '', '执行异常: ' + e.message);
  }

  return result;
};
