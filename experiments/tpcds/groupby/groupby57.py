# Created by Qingzhi Ma at 27/01/2020
# All right reserved
# Department of Computer Science
# the University of Warwick
# Q.Ma.2@warwick.ac.uk
#
#
# hive -e "select ss_store_sk, count(*) from store_sales_40g where ss_sold_date_sk between 2451119  and 2451483 group by ss_store_sk;" > ~/group57counts.csv
#
#
from dbestclient.executor.executor import SqlExecutor


def run():
    sqlExecutor = SqlExecutor()
    sqlExecutor.execute("set b_grid_search='False'")
    sqlExecutor.execute("set csv_split_char='|'")
    sqlExecutor.execute("set table_header=" +
                        "'ss_sold_date_sk|ss_sold_time_sk|ss_item_sk|ss_customer_sk|ss_cdemo_sk|ss_hdemo_sk|" +
                        "ss_addr_sk|ss_store_sk|ss_promo_sk|ss_ticket_number|ss_quantity|ss_wholesale_cost|" +
                        "ss_list_price|ss_sales_price|ss_ext_discount_amt|ss_ext_sales_price|" +
                        "ss_ext_wholesale_cost|ss_ext_list_price|ss_ext_tax|ss_coupon_amt|ss_net_paid|" +
                        "ss_net_paid_inc_tax|ss_net_profit|none'"
                        )

    build_models(sqlExecutor)
    query(sqlExecutor)


def build_models(sqlExecutor):
    # 10k
    # sqlExecutor.execute("create table ss40g(ss_sales_price real, ss_sold_date_sk real, ss_coupon_amt categorical) from '/data/tpcds/40G/ss_600k.csv' GROUP BY ss_store_sk method uniform size 600 scale data num_of_points2.csv", device='cpu', n_jobs=1)  # ,ss_quantity

    sqlExecutor.execute(
        "create table ss40g_no_categorical(ss_sales_price real, ss_sold_date_sk real,) from '/data/tpcds/40G/ss_600k.csv' GROUP BY ss_store_sk method uniform size 600 scale data num_of_points2.csv", device='cpu', n_jobs=1)


def query(sqlExecutor):
    # sqlExecutor.execute(
    #     "select avg(ss_sales_price)  from ss40g where   2451119  <=ss_sold_date_sk<= 2451483 and ss_coupon_amt=''  group by ss_store_sk", n_jobs=1, device='cpu')

    sqlExecutor.execute(
        "select avg(ss_sales_price)  from ss40g_no_categorical where   2451119  <=ss_sold_date_sk<= 2451483 group by ss_store_sk", n_jobs=1, device='cpu')


if __name__ == "__main__":
    run()
