import unittest

from interview_tool.typing_code import (_strip_skeleton_section, _type_prep)


class TypingMarkdownTests(unittest.TestCase):
    def test_markdown_skeleton_section_is_extracted(self):
        answer = """## 结论
补全函数体。

## 框架
```cpp
class Solution {
public:
    int solve() {
    }
};
```

## 代码
```cpp
class Solution {
public:
    int solve() {
        return 1;
    }
};
```
"""

        remaining, skeleton = _strip_skeleton_section(answer)

        self.assertNotIn("## 框架", remaining)
        self.assertIn("## 代码", remaining)
        self.assertIn("class Solution {", skeleton)

    def test_markdown_answer_still_types_only_final_code_block(self):
        answer = """## 结论
返回结果。

## 详细思路
- 直接计算。

## 代码
```js
function solve() {
    return 1;
}
```

## 复杂度
- 时间：`O(1)`
"""

        self.assertEqual(_type_prep(answer),
                         "function solve() {\n\treturn 1;\n}")


if __name__ == "__main__":
    unittest.main()
