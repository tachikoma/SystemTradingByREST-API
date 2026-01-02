# Integration tests scaffold

How to run

- Unit tests (fast, mock-based):

  - `pytest` (default does not include integration tests if you use markers)

- Integration (read-only) tests:

  - Set credentials and enable integration tests:

    ```bash
    export RUN_INTEGRATION=1
    export KIW_APPKEY=<your_appkey>
    export KIW_SECRET=<your_secret>
    pytest -m integration tests/test_integration_readonly.py -q
    ```

- Real-order tests (DANGEROUS — only use test account):

  ```bash
  export RUN_INTEGRATION=1
  export RUN_REAL_ORDERS=1
  export KIW_APPKEY=<your_appkey>
  export KIW_SECRET=<your_secret>
  pytest -m integration tests/test_integration_order.py -q
  ```

Notes

- Integration tests will skip automatically if `RUN_INTEGRATION` is not set to `1`.
- Keep API keys secure; never commit them to source control.
- Use a test/paper account when running order tests.
