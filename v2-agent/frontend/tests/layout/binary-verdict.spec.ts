import { expect, test } from "@playwright/test";
import { binaryVerdictLabel } from "../../src/binaryVerdict";

test("二元结论契约不会把低风险误判为 AI 生成", () => {
  expect(binaryVerdictLabel("低风险", 0.8)).toBe("真实图像");
  expect(binaryVerdictLabel("高风险", 0.2)).toBe("AI生成图像");
  expect(binaryVerdictLabel("需人工复核", 0.8)).toBe("AI生成图像");
  expect(binaryVerdictLabel("需人工复核", 0.2)).toBe("真实图像");
});
