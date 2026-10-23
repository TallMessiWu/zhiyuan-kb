import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn((media: string) => ({
    matches: false, media, onchange: null,
    addEventListener: vi.fn(), removeEventListener: vi.fn(),
  })),
});

// 与 theme.css 同源的浅色 token；jsdom 不做真实排版。
document.documentElement.style.cssText = `
  --surface: #FFFFFF; --accent-weak: #E1EFEB; --ink: #1B2420;
  --accent-line: #9CC8BD; --ink2: #49554E; --surface2: #EEF1EF;
  --surface3: #E6EAE7; --sans: sans-serif;
`;

afterEach(() => {
  cleanup();
  delete document.documentElement.dataset.theme;
  vi.clearAllMocks();
});
