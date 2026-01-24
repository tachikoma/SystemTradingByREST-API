from util.make_up_universe import execute_crawler as mu_execute_crawler, crawler as mu_crawler


def execute_crawler(output_file='all_stocks_naver.parquet'):
    """Wrapper delegating to `util.make_up_universe.execute_crawler`.

    Keeps the historical default filename but centralizes implementation.
    """
    return mu_execute_crawler(output_file=output_file)


def crawler(code, page, fields):
    """Delegate parsing to `make_up_universe.crawler` to avoid duplication.

    `fields` is required to keep this wrapper stateless.
    """
    return mu_crawler(code, page, fields)


if __name__ == "__main__":
    print('Start!')
    execute_crawler()
    print('End')