import unittest
from fastapi.testclient import TestClient
from app.main import app, DIST_DIR

class SpaTests(unittest.TestCase):
    def test_history_shell_is_public_but_business_api_is_not(self):
        client = TestClient(app)
        self.assertTrue((DIST_DIR/'index.html').is_file(), 'Run npm run build first')
        for path in ['/', '/workspace/login', '/workspace/contracts/a', '/workspace/results/a', '/workspace/reports/a']:
            response=client.get(path)
            self.assertEqual(response.status_code,200,path)
            self.assertIn('id="app"',response.text)
        self.assertEqual(client.get('/contracts').status_code,401)
        self.assertEqual(client.get('/reviews/a/items').status_code,401)
        self.assertEqual(client.get('/assets/not-present.js').status_code,404)
