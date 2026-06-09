/**
 * Translation prompt builder for markdown deliverable translation.
 *
 * Produces a prompt that instructs Claude to translate security assessment
 * reports from English to Chinese while preserving technical terms.
 */

/**
 * Build a translation prompt for a given markdown file.
 *
 * @param content - The English markdown content to translate
 * @param filename - Source filename for context in the prompt
 * @returns The full prompt string for runClaudePrompt
 */
export function buildTranslationPrompt(content: string, filename: string): string {
  return `You are a professional security report translator. Translate the following markdown document from English to Chinese.

## Translation Rules

1. **Preserve all markdown formatting exactly** — headings, lists, tables, code blocks, bold, links, images
2. **Keep these in English (do NOT translate):**
   - Vulnerability IDs (e.g., INJ-VULN-01, AUTH-VULN-10, XSS-VULN-02)
   - HTTP methods, paths, status codes, header names
   - URLs, file paths, code snippets, JSON field names
   - Technical abbreviations (XSS, SSRF, CSRF, RBAC, IDOR, SSO, BFF, SPA, OAuth, HMAC, AES, CSP, etc.)
   - Command names and CLI flags
3. **Severity levels — use bilingual format:** 严重 (Critical), 高 (High), 中 (Medium), 低 (Low)
4. **Translate narrative and descriptive text to natural, professional Chinese**
5. **Add a translation note** at the very top of the output as a blockquote:
   > 说明：本报告为英文版安全评估报告的中文翻译版。代码、命令、漏洞编号、HTTP 方法/状态码、文件路径、URL、header 名、JSON 字段名及标准技术缩写均保留英文原文，仅叙述性文字译为中文。
6. **Output ONLY the translated markdown** — no preamble, no explanation, no wrapping

## Source File: ${filename}

${content}`;
}
