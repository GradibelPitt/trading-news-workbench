import unittest

from pages.news.opensource_community.crawler import classify_domain


class OpenSourceClassifierTests(unittest.TestCase):
    def test_classifies_vector_database_without_false_rag_match(self):
        domain, keywords = classify_domain(
            "TurboVec: a fast vector index",
            "A vector database with Python bindings, faster than FAISS.",
        )
        self.assertEqual(domain, "数据与向量库")
        self.assertTrue("vector index" in keywords or "vector database" in keywords)

    def test_title_matches_outweigh_incidental_description_terms(self):
        domain, _ = classify_domain(
            "Agentic workflow framework",
            "Includes a small benchmark dataset and evaluation utility.",
        )
        self.assertEqual(domain, "AI Agent")

    def test_unknown_content_falls_back_to_other(self):
        domain, keywords = classify_domain("Tiny utility", "A pleasant little project")
        self.assertEqual(domain, "其他")
        self.assertEqual(keywords, [])


if __name__ == "__main__":
    unittest.main()
