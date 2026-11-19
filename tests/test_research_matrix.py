import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ResearchMatrixTests(unittest.TestCase):
    def test_working_matrix_has_56_sourced_aspects(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        self.assertEqual(m["status"], "under_review")
        self.assertFalse(m["canonical_model_changed"])
        self.assertIsNone(m["spatial_assignment"])
        self.assertEqual([p["id"] for p in m["pillars"]], [f"P{i}" for i in range(1, 8)])
        for p in m["pillars"]:
            self.assertEqual([s["number"] for s in p["sides"]], [1, 2, 3, 4])
            for s in p["sides"]:
                self.assertEqual(set(s["aspects"]), {"yes", "no"})
                for a in s["aspects"].values():
                    self.assertEqual(a["status"], "captured")
                    self.assertTrue(a["text"])
                    self.assertTrue(a["source"])

    def test_p2_voice_author_capture(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        p = m["pillars"][1]
        self.assertEqual([s["title"] for s in p["sides"]], ["Да", "Нет", "Воздержание", "Скольжение"])
        for s in p["sides"]:
            self.assertEqual(s["status"], "author_discussion")
            for a in s["aspects"].values():
                self.assertEqual(a["status"], "captured")
                self.assertIn("6961372944", a["source"])
        self.assertIn("задание", p["sides"][0]["aspects"]["yes"]["text"])
        self.assertIn("аномалию", p["sides"][1]["aspects"]["no"]["text"])
        self.assertIn("намерение", p["sides"][2]["aspects"]["yes"]["text"])
        self.assertIn("адресату", p["sides"][3]["aspects"]["yes"]["text"])
        self.assertIn("внутренний диалог", p["sides"][3]["aspects"]["no"]["text"])
        self.assertIn("ошибочными", p["revision_note"])
        self.assertEqual(m["pillars"][3]["sides"][3]["aspects"]["yes"]["status"], "captured")

    def test_p4_author_capture(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        p = m["pillars"][3]
        for s in p["sides"][:2]:
            for a in s["aspects"].values():
                self.assertEqual(a["status"], "captured")
                self.assertIn("6961642283", a["source"])
        self.assertEqual(p["sides"][3]["aspects"]["yes"]["status"], "captured")
        self.assertIn("двусторонний канал", p["sides"][3]["aspects"]["yes"]["text"])
        self.assertIn("6961828374", p["sides"][3]["aspects"]["yes"]["source"])
        preset = p["sides"][3]
        self.assertEqual(preset["aspects"]["no"]["status"], "captured")
        self.assertIn("6961772643", preset["aspects"]["no"]["source"])
        self.assertIn("пресеты", preset["aspects"]["no"]["text"])
        self.assertIn("4.4.2", preset["revision_note"])
        proposal = p["sides"][2]["candidate_note"]
        self.assertEqual(proposal["origin"], "assistant_proposal_not_author_confirmed")
        self.assertIsNone(proposal["aspect_assignment"])
        self.assertEqual(proposal["status"], "superseded")
        for a in p["sides"][2]["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6961715345", a["source"])
        self.assertIn("строить", p["sides"][2]["aspects"]["yes"]["text"])
        self.assertIn("без вмешательства", p["sides"][2]["aspects"]["no"]["text"])
        self.assertEqual(p["engineering_caution"]["origin"], "assistant_engineering_precaution")
        self.assertIn("пространство", p["sides"][0]["aspects"]["no"]["text"])
        self.assertIn("не выравнивается", p["sides"][1]["aspects"]["no"]["text"])

    def test_p5_execution_and_boundary(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        p = m["pillars"][4]
        s = p["sides"][0]
        for a in s["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6961900886", a["source"])
        self.assertIn("Голосом", s["aspects"]["yes"]["text"])
        self.assertIn("остановить", s["aspects"]["no"]["text"])
        self.assertIn("возможно skills", s["author_examples"]["no"])
        self.assertEqual(s["editorial_interpretation"]["origin"], "assistant_interpretation")
        second = p["sides"][1]
        for a in second["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6961963253", a["source"])
        self.assertIn("что сохранить", second["aspects"]["no"]["text"])
        self.assertEqual(second["accepted_example"]["origin"], "assistant_example_accepted_by_author")
        third = p["sides"][2]
        for a in third["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6962026026", a["source"])
        self.assertIn("поддерживающую", third["aspects"]["yes"]["text"])
        self.assertIn("условие окончания", third["aspects"]["no"]["text"])
        self.assertIn("вечный сон", third["author_example"]["text"])
        fourth = p["sides"][3]
        for a in fourth["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6962084983", a["source"])
        self.assertIn("одну задачу", fourth["aspects"]["yes"]["text"])
        self.assertIn("границы", fourth["aspects"]["no"]["text"])
        self.assertEqual(fourth["editorial_examples"]["origin"], "assistant_examples_not_author_conditions")

    def test_p6_verdict_and_authority_limits(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        p = m["pillars"][5]
        s = p["sides"][0]
        for a in s["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6962175481", a["source"])
        self.assertIn("вердикт", s["aspects"]["yes"]["text"])
        self.assertIn("старшая инстанция", s["aspects"]["no"]["text"])
        self.assertEqual(s["editorial_interpretation"]["origin"], "assistant_interpretation")
        self.assertIn("не назначен", s["limitations"][0])
        self.assertEqual(p["sides"][1]["aspects"]["yes"]["status"], "captured")
        self.assertIn("6962220801", p["sides"][1]["aspects"]["yes"]["source"])
        self.assertIn("якорь", p["sides"][1]["aspects"]["yes"]["text"])
        self.assertEqual(p["sides"][1]["aspects"]["no"]["status"], "captured")
        self.assertEqual(p["sides"][1]["aspects"]["no"]["origin"], "assistant_proposal_accepted_by_author")
        self.assertIn("6962476995", p["sides"][1]["aspects"]["no"]["source"])
        third = p["sides"][2]
        for a in third["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6962281095", a["source"])
        self.assertIn("присяжных", third["aspects"]["yes"]["text"])
        self.assertIn("существенное", third["aspects"]["no"]["text"])
        self.assertEqual(third["editorial_caution"]["origin"], "assistant_precaution_not_author_statement")
        fourth = p["sides"][3]
        for a in fourth["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertEqual(a["origin"], "assistant_proposal_accepted_by_author")
            self.assertIn("6962331900", a["source"])
        self.assertIn("ещё не вынесен", fourth["distinction"])
        self.assertIn("существенном факте", fourth["aspects"]["no"]["text"])

    def test_p7_keeper_name_and_storage(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        f = json.loads((ROOT / "model/research/foundation-20261114.json").read_text())
        for data in [m, f]:
            self.assertEqual(data["pillars"][6]["title"], "Хранитель")
            self.assertEqual(data["pillars"][6]["name_history"][0]["title"], "Итог и память")
        p = m["pillars"][6]
        for a in p["sides"][0]["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6962428263", a["source"])
        self.assertIn("не обязательная технология", p["sides"][0]["author_examples"]["yes"])
        for a in p["sides"][1]["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertEqual(a["origin"], "assistant_proposal_accepted_by_author")
            self.assertIn("6962542992", a["source"])
        for a in p["sides"][2]["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertEqual(a["origin"], "assistant_revision_accepted_after_author_correction")
            self.assertIn("6962647604", a["source"])
        self.assertIn("слепую зону", p["sides"][2]["aspects"]["no"]["text"])
        gliding = p["sides"][3]
        self.assertEqual(gliding["title"], "Скольжение")
        self.assertEqual(gliding["status"], "author_discussion")
        for a in gliding["aspects"].values():
            self.assertEqual(a["origin"], "author_discussion_editorial_paraphrase")
            self.assertIn("7019427154", a["source"])
        self.assertIn("степеней", gliding["aspects"]["yes"]["text"])
        self.assertIn("оракул всегда имеет доступ", gliding["aspects"]["no"]["text"])
        self.assertIn("границы доступа", " ".join(gliding["limitations"]))
        self.assertTrue((ROOT / gliding["decision_ref"]).is_file())
        draft = gliding["previous_draft"]
        self.assertEqual(draft["superseded_by"], gliding["aspects"]["yes"]["source"])
        self.assertEqual(draft["status"], "incomplete_draft")
        self.assertEqual(draft["origin"], "author_interrupted_statement")
        self.assertIn("6962691863", draft["source"])
        self.assertIn("степеней", draft["yes_fragment"])
        self.assertIn("всегда имеет доступ", draft["no_fragment"])

    def test_gliding_fixation_is_not_contract(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        s = m["pillars"][0]["sides"][3]
        self.assertIn("Скольжение далее", s["aspects"]["yes"]["text"])
        self.assertIn("точка фиксации", s["aspects"]["no"]["text"])
        self.assertIn("6876277084", s["aspects"]["no"]["source"])
        self.assertIn("не установлено", s["open_questions"][0])
        self.assertIn("весь рассматриваемый аспект", s["node_note"])

    def test_p1_abstention_preserves_editorial_origin(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        side = m["pillars"][0]["sides"][2]
        self.assertEqual(side["title"], "Воздержание")
        self.assertIn("6963072889", side["proposal_source"])
        self.assertTrue((ROOT / side["decision_ref"]).is_file())
        for a in side["aspects"].values():
            self.assertEqual(a["origin"], "assistant_proposal_accepted_by_author")
            self.assertIn("7019427154", a["source"])
        self.assertIn("Выслушать идею", side["aspects"]["yes"]["text"])
        self.assertIn("преждевременного суждения", side["aspects"]["no"]["text"])

    def test_author_numbering_and_candidates(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        f = json.loads((ROOT / "model/research/foundation-20261114.json").read_text())
        for data in [m, f]:
            self.assertEqual(data["pillars"][1]["title"], "Голос")
            self.assertEqual(data["pillars"][2]["title"], "Скелет связей")
            self.assertEqual(data["numbering_revision"]["previous_to_current"], {"P2": "P3", "P3": "P2"})
            self.assertFalse(data["numbering_revision"]["historical_sources_rewritten"])
        for p in m["pillars"]:
            for s in p["sides"]:
                self.assertEqual(s["id"], f'{p["id"]}.research.side.{s["number"]}')
        for candidate in f["pillars"][1]["candidates"]:
            self.assertEqual(candidate["status"], "under_review")
            self.assertTrue(candidate["id"].startswith("P2."))
        self.assertIn("подтверждён", m["pillars"][2]["open_questions"][0])
        self.assertIn("детализацию", m["pillars"][2]["sides"][0]["aspects"]["no"]["text"])
        self.assertIn("якорь", m["pillars"][2]["sides"][1]["aspects"]["yes"]["text"])

    def test_p3_return_and_observation(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        p = m["pillars"][2]
        self.assertIn("пока не найдена", p["sides"][1]["aspects"]["no"]["text"])
        self.assertIn("возврата", p["sides"][1]["aspects"]["yes"]["text"])
        self.assertIn("не натягивать", p["sides"][2]["aspects"]["no"]["text"])
        self.assertIn("6878777871", p["sides"][2]["aspects"]["yes"]["source"])
        self.assertEqual(p["sides"][2]["aspects"]["no"]["status"], "captured")
        gliding = p["sides"][3]
        self.assertIn("Движение вперёд", gliding["aspects"]["yes"]["text"])
        self.assertIn("Возврат внутрь", gliding["aspects"]["no"]["text"])
        self.assertIn("P3/2", gliding["aspects"]["no"]["text"])
        self.assertIn("затем сам исправил", gliding["revision_note"])
        for a in gliding["aspects"].values():
            self.assertEqual(a["status"], "captured")
            self.assertIn("6878834784", a["source"])

    def test_second_side_correction(self):
        m = json.loads((ROOT / "model/research/matrix-20261114.json").read_text())
        a = m["pillars"][0]["sides"][1]["aspects"]
        self.assertIn("ответы", a["yes"]["text"])
        self.assertIn("сомневаться", a["no"]["text"])
        self.assertIn("6875047183", a["yes"]["source"])
        canonical = json.loads((ROOT / "model/4i7.json").read_text())
        for p in canonical["pillars"]:
            for s in p["side_positions"].values():
                self.assertEqual(s["status"], "unassigned")

if __name__ == "__main__":
    unittest.main()

