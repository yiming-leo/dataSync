import logging
import re
import time

import pymysql
import redis


class Redis2Mysql:
    def __init__(self,
                 mysql_host: str,
                 mysql_port: int,
                 mysql_user: str,
                 mysql_password: str,
                 mysql_database: str,
                 redis_host: str,
                 redis_port: int,
                 redis_db: int,
                 batch_size: int,
                 db_name_list: list,
                 *table_structures):
        self.db_name_list = db_name_list  # 定义数据表名
        self.table_structures = table_structures  # 这是一个可变数量参数的变量
        self.batch_size = batch_size

        logging.info(f"self.table_structures: {self.table_structures}")

        try:
            # ------------初始化Redis------------
            self.redis_connection = redis.StrictRedis(host=redis_host, port=redis_port, db=redis_db)
            logging.info(f"\033[32m====Redis Connection Info====\n{self.redis_connection}\033[0m")
        except Exception as redis_error:
            logging.error(f"\033[31m====Failed to initialize Redis connection: {redis_error}\033[0m")

        try:
            # ------------初始化Mysql------------
            self.mysql_connection = pymysql.connect(host=mysql_host, port=mysql_port, user=mysql_user,
                                                    password=mysql_password, database=mysql_database)
            logging.info(f"\033[32m====Mysql Connection Info====\n{self.mysql_connection}\033[0m")
            self.mysql_cursor = self.mysql_connection.cursor()
            logging.debug(f"\033[34m====self.mysql_cursor====\n{self.mysql_cursor}\033[0m")
        except Exception as mysql_error:
            logging.error(f"\033[31m====Failed to initialize Mysql connection: {mysql_error}\033[0m")

    def sync_redis_2_mysql(self):
        try:
            # 定义数据表名
            # scrapy_xywy = 'scrapy_xywy'
            # scrapy_sprain_doc = 'scrapy_sprain_doc'
            # scrapy_haodf = 'scrapy_haodf'

            # table_structure_xywy = ['title', 'quest', 'url', 'answer', 'doc_name', 'doc_posts', 'department',
            #                         'hos_grade', 'hos_name']
            # table_structure_haodf = ['answer_url', 'disease', 'diseaseinfo', 'suggestions', 'grade', 'status',
            #                          'faculty', 'faculty_href']
            # table_structure_sprain = ['issue_title', 'issue_desc', 'case_url', 'answer', 'already_parsed']

            # batch_size = 10

            redis_results = self.redis_connection.keys('*')  # 以示例方式获取所有键
            redis_results = redis_results[:2]

            # redis_results = self.get_redis_keys(self.batch_size)
            logging.debug(f"redis_results: {redis_results}")

            for key in redis_results:
                key = key.decode('utf-8')
                logging.info(f"\033[32m====QUERY KEY====\n{key}\033[0m")
                # 获取Redis键的前缀
                prefix = key.split(':')[0]

                self.batch_sync_controller(key, prefix, self.db_name_list, self.table_structures)

        except pymysql.Error as mysql_error:
            logging.error(f"\033[31m====Mysql Error====\n{mysql_error}\033[0m")
        except redis.RedisError as redis_error:
            logging.error(f"\033[31m====Redis Error====\n{redis_error}\033[0m")
        finally:
            # 关闭 MySQL 连接和游标
            if 'mysql_cursor' in locals() and self.mysql_cursor is not None:
                self.mysql_cursor.close()
            if 'mysql_connection' in locals() and self.mysql_connection is not None:
                self.mysql_connection.close()
            # 关闭 Redis 连接
            if 'redis_connection' in locals() and self.redis_connection is not None:
                self.redis_connection.close()

    def batch_sync_controller(self, key: str, prefix: str, db_name_list: list, *table_structures):
        logging.info("\033[32m====Entering batch_sync_controller====\033[0m")
        value = self.redis_connection.hgetall(key)
        # --------解码所有键值对--------
        value = {k.decode('utf-8'): v.decode('utf-8') for k, v in value.items()}
        logging.info(f"\033[32m====redis value====\n{value}\033[0m")

        for db_name, table_structure in zip(db_name_list, *table_structures):
            # 匹配到需要处理的表
            pattern = re.compile(f'.*{re.escape(prefix)}.*')
            if pattern.match(db_name):
                logging.info(f"\033[32m====Processing====\n{db_name}\n====table_structure====\n{table_structure}")
                # ------------------------组装SQL区------------------------
                # TODO 需要将table_structure里的所有字段都整合成K-V结构以构造sql，就像下面一样
                data_to_insert = {field: f"'{value[field]}'" if isinstance(value[field], str) else value[field] for
                                  field in table_structure}
                logging.debug(f"\033[34m====data_to_insert====\n{data_to_insert}\033[0m")

                columns = ', '.join(data_to_insert.keys())
                logging.debug(f"\033[34m====columns====\n{columns}\033[0m")

                values = ', '.join(data_to_insert.values())
                logging.debug(f"\033[34m====values====\n{values}\033[0m")

                sql = f"INSERT INTO {db_name} ({columns}) VALUES ({values})"

                # ------------------------执行SQL区------------------------
                logging.debug(f"\033[34m====SQL====\n{sql}\033[0m")

                redis_pipe = self.redis_connection.pipeline()  # 获取 Redis 客户端对象
                redis_pipe.multi()  # 开启事务
                self.mysql_connection.begin()  # 开启事务

                try:
                    self.mysql_cursor.execute(sql)
                    self.mysql_connection.commit()
                    logging.info("\033[32m====Mysql row committed====\033[0m")
                    redis_pipe.delete(key)
                    redis_result = redis_pipe.execute()
                    logging.info(f"\033[32m====Redis row deleted:{redis_result}====\033[0m")
                except Exception as e:
                    # 发生异常，回滚事务
                    self.mysql_connection.rollback()
                    # self.redis_pipe.execute()  # 自动回滚
                    logging.error(f"\033[31m====SQL Error Occurred: {e}====\033[0m")
                    raise e
        time.sleep(1)  # 休息一下吧

    # redis key扫描模块
    def redis_scan(self, count):
        cursor = '0'
        keys = []
        while True:
            cursor, partial_keys = self.redis_connection.scan(cursor=cursor, count=count)
            keys.extend(partial_keys)
            if cursor == '0':  # 如果迭代结束，退出循环
                break
        return keys

    # redis key获取模块
    def get_redis_keys(self, count):
        # 使用 SCAN 命令来获取匹配的键，count 表示每次迭代获取的元素数量
        redis_results = self.redis_scan(count)
        return redis_results
