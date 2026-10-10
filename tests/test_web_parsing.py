"""연령별 학급 현황 표 파싱 계약. 화면 표기가 달라도 같은 결과를 내야 한다."""

import sys
import unittest

sys.path.insert(0, "D:/OneDrive/AI/kaic-kinder-info")

import kinderweb


def classes_html(capacity_label):
    return f"""
    <h4>연령별 학급 현황</h4>
    <table>
      <tr>
        <th>인가총정원</th><th>총현원</th><th>구분</th>
        <th>만3세반</th><th>만4세반</th><th>만5세반</th>
        <th>만 3~4세</th><th>만 4~5세</th><th>만 3~5세</th>
        <th>특수학급</th>
      </tr>
      <tr>
        <td>96</td><td>88</td><td>학급 수</td>
        <td>-</td><td>2</td><td>1</td><td>4</td><td>-</td><td>1</td><td>-</td>
      </tr>
      <tr>
        <td>96</td><td>88</td><td>{capacity_label}</td>
        <td>-</td><td>40</td><td>20</td><td>88</td><td>-</td><td>24</td><td>-</td>
      </tr>
      <tr>
        <td>96</td><td>88</td><td>현원</td>
        <td>-</td><td>38</td><td>20</td><td>88</td><td>-</td><td>23</td><td>-</td>
      </tr>
    </table>
    """


class AgeClassTableTests(unittest.TestCase):
    def test_parses_the_legacy_capacity_row_label(self):
        parsed = kinderweb.parse_age_classes(classes_html("정원"))
        self.assertEqual(parsed["학급"]["3-4세"], {"학급": 4, "정원": 88, "현원": 88})
        self.assertEqual(parsed["학급"]["4-5세"], {"학급": None, "정원": None, "현원": None})
        self.assertEqual(parsed["학급"]["3-5세"], {"학급": 1, "정원": 24, "현원": 23})
        self.assertEqual(parsed["인가총정원"], 96)
        self.assertEqual(parsed["총현원"], 88)

    def test_treats_the_recruitment_capacity_label_as_the_same_row(self):
        parsed = kinderweb.parse_age_classes(classes_html("모집정원"))
        self.assertEqual(parsed["학급"]["만4세"], {"학급": 2, "정원": 40, "현원": 38})
        self.assertEqual(parsed["학급"]["3-4세"], {"학급": 4, "정원": 88, "현원": 88})
        self.assertEqual(parsed["인가총정원"], 96)
        self.assertEqual(parsed["총현원"], 88)

    def test_missing_capacity_row_still_raises_a_parse_error(self):
        html = classes_html("정원").replace("<td>정원</td>", "<td> </td>")
        with self.assertRaises(kinderweb.ParseChanged):
            kinderweb.parse_age_classes(html)


if __name__ == "__main__":
    unittest.main()