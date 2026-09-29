"""Offline tests for labpub.  Run:  python3 tools/labpub/test_labpub.py

The Crossref and ORCID responses below are recorded in the shape those APIs
return, so the parsing can be checked without a network connection.
"""
import copy
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import labpub  # noqa: E402

CFG = labpub.load_config()
ORIG_ORCID_WORKS = labpub.orcid_works
ORIG_FROM_CROSSREF = labpub.from_crossref

CR_SPRINGER_CONF = {  # a Springer LNNS conference paper, typed as a book chapter
    "type": "book-chapter", "DOI": "10.1007/978-3-032-99999-9_7",
    "title": ["Recurrent Dynamics and the Grammar of Working Memory"],
    "author": [{"given": "Rakesh", "family": "Sengupta"}, {"given": "Lekhana Priya", "family": "Gundapaneni"}],
    "container-title": ["Lecture Notes in Networks and Systems",
                        "Proceedings of International Conference on Computing Systems and Intelligent Applications"],
    "event": {"name": "International Conference on Computing Systems and Intelligent Applications", "acronym": "ComSIA"},
    "publisher": "Springer Nature Switzerland", "volume": "2071", "page": "88-99",
    "published-print": {"date-parts": [[2027, 2, 3]]},
}
CR_IEEE = {
    "type": "proceedings-article", "DOI": "10.1109/ICCCNT61001.2025.1234567",
    "title": ["Attractor Counting in Shunting Networks"],
    "author": [{"given": "Rakesh", "family": "Sengupta"}, {"given": "Sang-Ah", "family": "Yoo"}],
    "container-title": ["2025 16th International Conference on Computing Communication and Networking Technologies (ICCCNT)"],
    "publisher": "IEEE", "page": "1-6", "issued": {"date-parts": [[2025, 7]]},
}
CR_JOURNAL = {
    "type": "journal-article", "DOI": "10.1038/S41598-026-00001-X",
    "title": ["A <i>Test</i> of Numerosity"], "subtitle": ["Evidence from Recurrent Networks"],
    "author": [{"given": "Bhavesh", "family": "Verma"}, {"given": "Rakesh", "family": "Sengupta"}],
    "container-title": ["Scientific Reports"], "publisher": "Springer Science and Business Media LLC",
    "volume": "16", "issue": "1", "page": "4501", "published-online": {"date-parts": [[2026, 3, 9]]},
}
CR_CHAPTER = {
    "type": "book-chapter", "DOI": "10.4018/978-1-0000-0000-0.ch005",
    "title": ["The Machine Does Not Blink"],
    "author": [{"given": "Rakesh", "family": "Sengupta"}, {"given": "Aahana", "family": "Rath"}],
    "editor": [{"given": "Nguyen", "family": "Son"}],
    "container-title": ["Visual Culture at the Intersection of Human and Machine Vision"],
    "publisher": "IGI Global", "page": "101-128", "issued": {"date-parts": [[2027]]},
}

ORCID = {"group": [
    {"external-ids": {"external-id": [{"external-id-type": "doi",
                                       "external-id-value": "https://doi.org/10.1038/s41598-023-44535-3"}]},
     "work-summary": [{"title": {"title": {"value": "Emergence of behavioral phenomena ..."}},
                       "type": "journal-article", "publication-date": {"year": {"value": "2023"}}}]},
    {"external-ids": {"external-id": [{"external-id-type": "doi",
                                       "external-id-value": "10.1109/ICCCNT61001.2025.1234567"}]},
     "work-summary": [{"title": {"title": {"value": "Attractor Counting in Shunting Networks"}},
                       "type": "conference-paper", "publication-date": {"year": {"value": "2025"}}}]},
    {"external-ids": {"external-id": []},
     "work-summary": [{"title": {"title": {"value": "A brand new essay on temporal capital and care"}},
                       "type": "journal-article", "publication-date": {"year": {"value": "2026"}},
                       "journal-title": {"value": "Time & Society"}}]},
    {"external-ids": {"external-id": []},       # same paper as one already listed, no DOI
     "work-summary": [{"title": {"title": {"value": "How Embodied Is Time?"}},
                       "type": "journal-article", "publication-date": {"year": {"value": "2018"}}}]},
]}


class Parsing(unittest.TestCase):
    def test_initials(self):
        self.assertEqual(labpub.initials("Lekhana Priya"), "L. P.")
        self.assertEqual(labpub.initials("Sang-Ah"), "S.-A.")
        self.assertEqual(labpub.fmt_author("Rakesh", "Sengupta"), "R. Sengupta")

    def test_doi_normalisation(self):
        self.assertEqual(labpub.norm_doi("https://doi.org/10.1038/ABC"), "10.1038/abc")
        self.assertEqual(labpub.norm_doi("doi: 10.1/X"), "10.1/x")

    def test_springer_conference_becomes_proceedings(self):
        p = labpub.from_crossref("", CFG, CR_SPRINGER_CONF)
        self.assertEqual(p["type"], "proceedings")
        self.assertEqual(p["venue"], "ComSIA 2027")
        self.assertEqual(p["series"], "Lecture Notes in Networks and Systems")
        self.assertEqual(p["publisher"], "Springer")
        self.assertEqual(p["pages"], "88–99")
        self.assertEqual(p["authors"], ["R. Sengupta", "L. P. Gundapaneni"])
        self.assertEqual(p["year"], 2027)

    def test_ieee_short_venue(self):
        p = labpub.from_crossref("", CFG, CR_IEEE)
        self.assertEqual(p["venue"], "ICCCNT 2025")
        self.assertEqual(p["publisher"], "IEEE")
        self.assertEqual(p["authors"][1], "S.-A. Yoo")

    def test_journal(self):
        p = labpub.from_crossref("", CFG, CR_JOURNAL)
        self.assertEqual(p["type"], "journal")
        self.assertEqual(p["venue"], "Scientific Reports")
        self.assertEqual(p["title"], "A Test of Numerosity: Evidence from Recurrent Networks")
        self.assertEqual(p["doi"], "10.1038/s41598-026-00001-x")
        self.assertEqual((p["volume"], p["issue"], p["pages"]), ("16", "1", "4501"))

    def test_chapter_with_editors(self):
        p = labpub.from_crossref("", CFG, CR_CHAPTER)
        self.assertEqual(p["type"], "chapter")
        self.assertEqual(p["book"], "Visual Culture at the Intersection of Human and Machine Vision")
        self.assertEqual(p["editors"], ["N. Son"])

    def test_sentence_case_keeps_acronyms_and_names(self):
        s = labpub.sentence_case("Alpha Band EEG and the Spinozist Critique: A New Account", CFG)
        self.assertEqual(s, "Alpha band EEG and the Spinozist critique: A new account")


class Rendering(unittest.TestCase):
    def test_tex_marks_me_and_students(self):
        p = {"type": "proceedings", "year": 2026, "authors": ["J. G. Lazarus", "R. Sengupta"],
             "title": "Something & more_stuff", "venue": "FACEIT 2026", "publisher": "Springer",
             "doi": "10.1007/x_1"}
        t = labpub.render_tex(p, CFG)
        self.assertIn(r"\stu{J.~G.~Lazarus} \& \me\ (2026)", t)
        self.assertIn(r"Something \& more\_stuff.", t)
        self.assertIn(r"\doi{10.1007/x_1}", t)            # DOI left raw for the \doi macro

    def test_generated_cv_counts(self):
        tex = labpub.build_cv(labpub.load_db(CFG), CFG)
        self.assertEqual(tex.count(r"\begin{publist}{journals}{J}"), 1)
        self.assertIn(r"\begin{publist}{proceedings}{C}", tex)
        self.assertEqual(tex.count(r"\item"), 46)


class Orcid(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = copy.deepcopy(CFG)
        src = labpub.data_path(CFG)
        (self.tmp / "data").mkdir()
        shutil.copy(src, self.tmp / "data" / "publications.json")
        self.patch = mock.patch.object(labpub, "REPO", self.tmp)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp)

    def test_matching(self):
        works = labpub.orcid_works(self.cfg, ORCID)
        pubs = labpub.load_db(self.cfg)["publications"]
        found = [labpub.find_match(w, pubs) for w in works]
        self.assertEqual(found[0]["id"], "verma2023emergence")     # by DOI
        self.assertIsNone(found[1])                                  # new, has DOI
        self.assertIsNone(found[2])                                  # new, no DOI
        self.assertEqual(found[3]["id"], "sengupta2018embodied")    # by title, no DOI

    def test_apply_non_interactive(self):
        report = self.tmp / "report.md"
        args = mock.Mock(apply=True, yes=True, report=str(report))

        def fake_crossref(doi, cfg, message=None):
            return ORIG_FROM_CROSSREF(doi, cfg, CR_IEEE)

        with mock.patch.object(labpub, "orcid_works", lambda cfg: ORIG_ORCID_WORKS(cfg, ORCID)), \
             mock.patch.object(labpub, "from_crossref", side_effect=fake_crossref), \
             redirect_stdout(io.StringIO()):
            labpub.cmd_orcid(args, self.cfg)
        db = labpub.load_db(self.cfg)
        new = [p for p in db["publications"] if p.get("source") == "orcid"]
        self.assertEqual(len(new), 2)
        self.assertTrue(all(p.get("review") for p in new))
        ieee = next(p for p in new if p.get("doi"))
        self.assertEqual(ieee["venue"], "ICCCNT 2025")
        nodoi = next(p for p in new if not p.get("doi"))
        self.assertIn("TODO", nodoi["note"])
        self.assertIn("ICCCNT 2025", report.read_text())
        errors, warnings = labpub.validate(db, self.cfg)
        self.assertEqual(errors, [])
        self.assertTrue(any("not yet reviewed" in w for w in warnings))

    def test_crossref_failure_falls_back_to_orcid_summary(self):
        args = mock.Mock(apply=True, yes=True, report=None)
        with mock.patch.object(labpub, "orcid_works", lambda cfg: ORIG_ORCID_WORKS(cfg, ORCID)), \
             mock.patch.object(labpub, "from_crossref", side_effect=OSError("offline")), \
             redirect_stdout(io.StringIO()):
            labpub.cmd_orcid(args, self.cfg)
        new = [p for p in labpub.load_db(self.cfg)["publications"] if p.get("source") == "orcid"]
        self.assertEqual(len(new), 2)
        self.assertTrue(all(p["authors"] == ["R. Sengupta"] for p in new))


class Push(unittest.TestCase):
    def test_commit_and_push_to_remote(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            remote, work = tmp / "remote.git", tmp / "work"
            run = lambda *a, cwd=None: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)
            run("git", "init", "--bare", "-b", "main", str(remote))
            run("git", "clone", str(remote), str(work))
            run("git", "config", "user.email", "t@example.com", cwd=work)
            run("git", "config", "user.name", "test", cwd=work)
            (work / "data").mkdir()
            shutil.copy(labpub.data_path(CFG), work / "data" / "publications.json")
            run("git", "add", ".", cwd=work)
            run("git", "commit", "-m", "init", cwd=work)
            run("git", "push", "-u", "origin", "main", cwd=work)
            with mock.patch.object(labpub, "REPO", work):
                db = labpub.load_db(CFG)
                p = labpub.from_crossref("", CFG, CR_JOURNAL)
                p.update(cats=["numerosity"], web=True)
                p["id"] = labpub.make_id(p, {x["id"] for x in db["publications"]})
                labpub.insert(db, p)
                labpub.save_db(CFG, db)
                with redirect_stdout(io.StringIO()):
                    labpub.cmd_push(mock.Mock(message=None, no_push=False), CFG)
            log = run("git", "--git-dir", str(remote), "log", "--oneline", "-1").stdout
            self.assertIn("labpub: add verma2026test", log)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
