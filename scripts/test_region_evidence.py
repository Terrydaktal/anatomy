#!/usr/bin/env python3
"""Check that terminology and fitted geometry cannot masquerade as supplied anatomy."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sync_region_evidence as evidence


TERMS = b'"TA2ID;English;Latin"\n"429;Pterygopalatine fossa;Fossa pterygopalatina"\n"600;Pterygoid fossa;Fossa pterygoidea"\n"557;Occipital condyle;Condylus occipitalis"\n"482;(Cranio-orbital foramen);(Foramen cranioorbitale)"\n'
MESHES = b'<table><tr><td class="art_id">FJ6412</td><td class="rep_id">BP29493</td><td class="cdi_name">FMA52891</td><td class="cdi_name_e">Perpendicular plate of ethmoid</td><td class="cdi_syn_e">Lamina perpendicularis</td></tr></table>'


class RegionEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = evidence.build_report(evidence.ROOT, TERMS, MESHES)

    def test_exact_names_keep_distinct_fossae(self):
        index = evidence.terminology_index(TERMS)
        first = evidence.terminology_matches({"name": "Left pterygopalatine fossa"}, index)
        second = evidence.terminology_matches({"name": "Right pterygoid fossa"}, index)
        self.assertEqual([term["ta2_id"] for term in first], ["429"])
        self.assertEqual([term["ta2_id"] for term in second], ["600"])
        self.assertEqual(evidence.terminology_matches({"name": "Unknown fossa"}, index), [])

    def test_names_do_not_supply_boundaries(self):
        region = next(item for item in self.report["regions"] if item["id"] == "left-pterygoid-fossa")
        self.assertEqual(region["terminology"][0]["ta2_id"], "600")
        for dataset in ("reference", "ct"):
            self.assertFalse(region["datasets"][dataset]["supplied_boundary"])
            self.assertEqual(region["datasets"][dataset]["kind"], "location-guide")
            self.assertEqual(region["datasets"][dataset]["scope"], "no-painted-region")

    def test_variable_orbital_names_remain_unlocated(self):
        for side in ['left', 'right']:
            region = next(item for item in self.report['regions'] if item['id'] == f'{side}-cranio-orbital-foramen')
            self.assertEqual([term['ta2_id'] for term in region['terminology']], ['482'])
            for dataset in ['reference', 'ct']:
                self.assertEqual(region['datasets'][dataset]['kind'], 'unlocated')
                self.assertEqual(region['datasets'][dataset]['scope'], 'no-location-no-painted-region')
                self.assertFalse(region['datasets'][dataset]['supplied_boundary'])

    def test_registered_sources_remain_estimates(self):
        region = next(item for item in self.report['regions'] if item['id'] == 'left-pterygopalatine-fossa')
        for dataset in ['reference', 'ct']:
            fitted = region['datasets'][dataset]
            self.assertEqual(fitted['kind'], 'registered-source-region')
            self.assertEqual(fitted['source_ids'], ['fossafinder'])
            self.assertFalse(fitted['supplied_boundary'])
            self.assertFalse(fitted['independently_validated'])
            self.assertIsNone(fitted['anatomical_accuracy_percent'])

    def test_masks_are_not_transferred_to_another_specimen(self):
        self.assertEqual(self.report["models"]["reference"]["supplied_boundaries"], 14)
        self.assertEqual(self.report["models"]["ct"]["supplied_boundaries"], 0)
        condyle = next(item for item in self.report["regions"] if item["id"] == "left-occipital-condyle")
        self.assertTrue(condyle["datasets"]["reference"]["supplied_boundary"])
        self.assertFalse(condyle["datasets"]["ct"]["supplied_boundary"])
        self.assertEqual(condyle["datasets"]["reference"]["scope"], ["articular-surface-of-feature"])

    def test_real_opening_fits_remain_estimates(self):
        fits = [item["datasets"]["ct"] for item in self.report["regions"] if item["datasets"]["ct"].get('method') == 'native-depth-fitted-aperture-rim']
        self.assertEqual(len(fits), 4)
        self.assertTrue(all(not fit["supplied_boundary"] and not fit["independently_validated"] for fit in fits))

    def test_junction_points_are_not_anatomical_surface_regions(self):
        for identity in ['bregma', 'lambda']:
            entry = next(item for item in self.report['regions'] if item['id'] == identity)
            for dataset in ['reference', 'ct']:
                fitted = entry['datasets'][dataset]
                self.assertEqual(fitted['method'], 'native-suture-junction')
                self.assertEqual(fitted['scope'], 'point-landmark-visualization')
                self.assertFalse(fitted['supplied_boundary'])
                self.assertFalse(fitted['independently_validated'])

    def test_external_mesh_names_do_not_prove_registration(self):
        region = next(item for item in self.report["regions"] if item["id"] == "perpendicular-plate-of-ethmoid")
        self.assertEqual(region["external_mesh_candidates"][0]["mesh_id"], "FJ6412")
        self.assertEqual(region["external_mesh_candidates"][0]["status"], "unregistered-candidate")
        self.assertTrue(all(not item["supplied_boundary"] for item in region["datasets"].values()))

    def test_html_text_and_synonyms_are_parsed_without_substring_matches(self):
        index, count = evidence.external_mesh_index(MESHES)
        self.assertEqual(count, 1)
        self.assertEqual(evidence.external_candidates({"name": "Lamina perpendicularis"}, [], index)[0]["mesh_id"], "FJ6412")
        self.assertEqual(evidence.external_candidates({"name": "Plate"}, [], index), [])

    def test_no_fabricated_accuracy_percentage(self):
        self.assertIsNone(self.report["anatomical_accuracy_percent"])
        for region in self.report["regions"]:
            for item in region["datasets"].values():
                if item["available"]:
                    self.assertIsNone(item["anatomical_accuracy_percent"])
                    self.assertFalse(item["independently_validated"])

    def test_changed_reference_model_is_rejected(self):
        original = evidence.checked_json
        def changed(path):
            value = copy.deepcopy(original(path))
            if path.name == "source-regions.json":
                value["source"]["model_sha256"] = "0" * 64
            return value
        with patch.object(evidence, "checked_json", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "different reference model"):
                evidence.build_report(evidence.ROOT, TERMS)

    def test_corrupted_binary_and_cached_download_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "mask.bin").write_bytes(b"changed")
            manifest = {"binary": {"url": "mask.bin", "byte_length": 7, "sha256": hashlib.sha256(b"correct").hexdigest()}}
            with self.assertRaisesRegex(ValueError, "binary checksum"):
                evidence.check_binary(root, manifest)
            with self.assertRaisesRegex(ValueError, "cache checksum"):
                evidence.terminology_bytes(root / "mask.bin", offline=True)

    def test_automatic_coverage_gate_fails_for_missing_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with patch("sys.argv", ["sync_region_evidence.py", "--offline", "--require-source-boundaries", "--output", str(output)]), patch.object(evidence, "terminology_bytes", return_value=TERMS), patch.object(evidence, "model_index_bytes", return_value=MESHES), patch("builtins.print"):
                with self.assertRaises(SystemExit) as error:
                    evidence.main()
            self.assertEqual(error.exception.code, 2)
            self.assertFalse(json.loads(output.read_text())["models"]["ct"]["complete_source_boundary_coverage"])


if __name__ == "__main__":
    unittest.main()
