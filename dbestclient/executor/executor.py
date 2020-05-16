# Created by Qingzhi Ma at 2019-07-23
# All right reserved
# Department of Computer Science
# the University of Warwick
# Q.Ma.2@warwick.ac.uk

import os
import os.path
import warnings
from datetime import datetime
from multiprocessing import set_start_method as set_start_method_cpu

import dill
import numpy as np
import torch
from torch.multiprocessing import set_start_method as set_start_method_torch

from dbestclient.catalog.catalog import DBEstModelCatalog
from dbestclient.executor.queryengine import QueryEngine
from dbestclient.executor.queryenginemdn import (MdnQueryEngine,
                                                 MdnQueryEngineBundle,
                                                 MdnQueryEngineXCategorical)
from dbestclient.io.sampling import DBEstSampling
from dbestclient.ml.modeltrainer import (GroupByModelTrainer, KdeModelTrainer,
                                         SimpleModelTrainer)
from dbestclient.ml.modelwraper import (GroupByModelWrapper,
                                        get_pickle_file_name)
from dbestclient.parser.parser import DBEstParser
from dbestclient.tools.dftools import (get_group_count_from_df,
                                       get_group_count_from_summary_file,
                                       get_group_count_from_table)
from dbestclient.tools.running_parameters import RUNTIME_CONF, DbestConfig


class SqlExecutor:
    """
    This is the executor for the SQL query.
    """

    def __init__(self):
        self.parser = None
        self.config = DbestConfig()  # model-related configuration
        self.runtime_config = RUNTIME_CONF
        self.last_config = None
        self.model_catalog = DBEstModelCatalog()
        self.init_model_catalog()
        self.save_sample = False
        # self.table_header = None
        self.n_total_records = None
        self.use_kde = True

    def init_model_catalog(self):
        # search the warehouse, and add all available models.
        n_model = 0
        for file_name in os.listdir(self.config.get_config()['warehousedir']):

            # load simple models
            if file_name.endswith(".pkl"):
                if n_model == 0:
                    print("start loading pre-existing models.")

                with open(self.config.get_config()['warehousedir'] + "/" + file_name, 'rb') as f:
                    model = dill.load(f)
                self.model_catalog.model_catalog[model.init_pickle_file_name(
                )] = model
                n_model += 1

            # load group by models
            if os.path.isdir(self.config.get_config()['warehousedir'] + "/" + file_name):
                n_models_in_groupby = 0
                if n_model == 0:
                    print("start loading pre-existing models.")

                for model_name in os.listdir(self.config.get_config()['warehousedir'] + "/" + file_name):
                    if model_name.endswith(".pkl"):
                        with open(self.config.get_config()['warehousedir'] + "/" + file_name + "/" + model_name, 'rb') as f:
                            model = dill.load(f)
                            n_models_in_groupby += 1

                        if n_models_in_groupby == 1:
                            groupby_model_wrapper = GroupByModelWrapper(model.mdl, model.tbl, model.x, model.y,
                                                                        model.groupby_attribute,
                                                                        x_min_value=model.x_min_value,
                                                                        x_max_value=model.x_max_value)
                        groupby_model_wrapper.add_simple_model(model)

                    self.model_catalog.model_catalog[file_name] = groupby_model_wrapper.models
                    n_model += 1

        if n_model > 0:
            print("Loaded " + str(n_model) + " models.")

    def execute(self, sql):
        # b_use_gg=False, n_per_gg=10, result2file=None,n_mdn_layer_node = 10, encoding = "onehot",n_jobs = 4, b_grid_search = True,device = "cpu", n_division = 20
        # prepare the parser
        if type(sql) == str:
            self.parser = DBEstParser()
            self.parser.parse(sql)
        elif type(sql) == DBEstParser:
            self.parser = sql
        else:
            print("Unrecognized SQL! Please check it!")
            exit(-1)

        # execute the query
        if self.parser.if_nested_query():
            warnings.warn("Nested query is currently not supported!")
        else:
            sql_type = self.parser.get_query_type()
            if sql_type == "ddl":
                # initialize the configure for each model creation.
                if self.last_config:
                    self.config = self.last_config
                else:
                    self.config = DbestConfig()
                # DDL, create the model as requested
                mdl = self.parser.get_ddl_model_name()
                tbl = self.parser.get_from_name()

                if self.parser.if_model_need_filter():
                    self.config.set_parameter("accept_filter", True)

                # remove unnecessary charactor '
                tbl = tbl.replace("'", "")
                if os.path.isfile(tbl):  # the absolute path is provided
                    original_data_file = tbl
                else:  # the file is in the warehouse direcotry
                    original_data_file = self.config.get_config()[
                        'warehousedir'] + "/" + tbl
                yheader = self.parser.get_y()

                xheader_continous, xheader_categorical = self.parser.get_x()

                ratio = self.parser.get_sampling_ratio()
                method = self.parser.get_sampling_method()
                table_header = self.config.get_config()['table_header']
                # print("table_header", table_header)
                if table_header is not None:
                    table_header = table_header.split(
                        self.config.get_config()['csv_split_char'])

                # make samples
                if not self.parser.if_contain_groupby():  # if group by is not involved
                    sampler = DBEstSampling(
                        headers=table_header, usecols={"y": yheader, "x_continous": xheader_continous, "x_categorical": xheader_categorical, "gb": None})
                else:
                    groupby_attribute = self.parser.get_groupby_value()
                    sampler = DBEstSampling(headers=table_header, usecols={
                                            "y": yheader, "x_continous": xheader_continous, "x_categorical": xheader_categorical, "gb": groupby_attribute})

                # print(self.config)
                if os.path.exists(self.config.get_config()['warehousedir'] + "/" + mdl + '.pkl'):
                    print(
                        "Model {0} exists in the warehouse, please use"
                        " another model name to train it.".format(mdl))
                    return
                # if self.parser.if_contain_groupby():
                #     groupby_attribute = self.parser.get_groupby_value()
                #     if os.path.exists(self.config['warehousedir'] + "/" + mdl + "_groupby_" + groupby_attribute):
                #         print(
                #             "Model {0} exists in the warehouse, please use"
                #             " another model name to train it.".format(mdl))
                #         return
                print("Start creating model " + mdl)
                time1 = datetime.now()

                if self.save_sample:
                    sampler.make_sample(
                        original_data_file, ratio, method, split_char=self.config.get_config()[
                            'csv_split_char'],
                        file2save=self.config.get_config()['warehousedir'] +
                        "/" + mdl + '.csv',
                        num_total_records=self.n_total_records)
                else:
                    sampler.make_sample(
                        original_data_file, ratio, method, split_char=self.config.get_config()[
                            'csv_split_char'],
                        num_total_records=self.n_total_records)

                # set the n_total_point and scaling factor for each model.
                # self.config.set_parameter(
                #     "n_total_point", sampler.n_total_point)
                # self.config.set_parameter(
                #     "scaling_factor", sampler.scaling_factor)
                # print("scaling_factor is ", sampler.scaling_factor)

                if not self.parser.if_contain_groupby():  # if group by is not involved

                    # n_total_point = sampler.n_total_point
                    # xys = sampler.getyx(yheader, xheader_continous)

                    # simple_model_wrapper = SimpleModelTrainer(mdl, tbl, xheader_continous, yheader,
                    #                                           n_total_point, ratio, config=self.config.copy()).fit_from_df(
                    #     xys, self.runtime_config)

                    # reg = simple_model_wrapper.reg
                    # density = simple_model_wrapper.density
                    # n_sample_point = int(simple_model_wrapper.n_sample_point)
                    # n_total_point = int(simple_model_wrapper.n_total_point)
                    # x_min_value = float(simple_model_wrapper.x_min_value)
                    # x_max_value = float(simple_model_wrapper.x_max_value)
                    # query_engine = QueryEngine(mdl, reg, density, n_sample_point,
                    #                            n_total_point, x_min_value, x_max_value, xheader_continous[
                    #                                0],
                    #                            self.config)
                    sampler.sample.sampledf["dummy_gb"] = "dummy"
                    sampler.sample.usecols = {"y": yheader, "x_continous": xheader_continous,
                                              "x_categorical": xheader_categorical, "gb": "dummy_gb"}
                    n_total_point, xys = sampler.get_groupby_frequency_data()
                    # if not n_total_point['if_contain_x_categorical']:
                    n_total_point.pop("if_contain_x_categorical")
                    kdeModelWrapper = KdeModelTrainer(
                        mdl, tbl, xheader_continous[0], yheader,
                        groupby_attribute=["dummy_gb"],
                        groupby_values=list(
                            n_total_point.keys()),
                        n_total_point=n_total_point,
                        x_min_value=-np.inf, x_max_value=np.inf,
                        config=self.config.copy()).fit_from_df(
                        xys["data"], self.runtime_config, network_size="large")

                    qe_mdn = MdnQueryEngine(
                        kdeModelWrapper, config=self.config.copy())

                    qe_mdn.serialize2warehouse(
                        self.config.get_config()['warehousedir'])
                    self.model_catalog.add_model_wrapper(qe_mdn)

                else:  # if group by is involved in the query
                    if self.config.get_config()['reg_type'] == "qreg":
                        xys = sampler.getyx(yheader, xheader_continous)
                        n_total_point = get_group_count_from_table(
                            original_data_file, groupby_attribute, sep=self.config.get_config()[
                                'csv_split_char'],
                            headers=table_header)

                        n_sample_point = get_group_count_from_df(
                            xys, groupby_attribute)
                        groupby_model_wrapper = GroupByModelTrainer(mdl, tbl, xheader_continous, yheader, groupby_attribute,
                                                                    n_total_point, n_sample_point,
                                                                    x_min_value=-np.inf, x_max_value=np.inf,
                                                                    config=self.config.copy()).fit_from_df(
                            xys, self.runtime_config)
                        groupby_model_wrapper.serialize2warehouse(
                            self.config.get_config()['warehousedir'] + "/" + groupby_model_wrapper.dir)
                        self.model_catalog.model_catalog[groupby_model_wrapper.dir] = groupby_model_wrapper.models
                    else:  # "mdn"

                        xys = sampler.getyx(
                            yheader, xheader_continous, groupby=groupby_attribute)
                        # xys[groupby_attribute] = pd.to_numeric(xys[groupby_attribute], errors='coerce')
                        # xys=xys.dropna(subset=[yheader, xheader,groupby_attribute])

                        # n_total_point = get_group_count_from_table(
                        #     original_data_file, groupby_attribute, sep=',',#self.config['csv_split_char'],
                        #     headers=self.table_header)
                        if isinstance(ratio, str):
                            frequency_file = self.config.get_config()[
                                'warehousedir'] + "/" + ratio
                            # "/num_of_points.csv"
                            if os.path.exists(frequency_file):
                                n_total_point = get_group_count_from_summary_file(
                                    frequency_file, sep=',')
                                n_total_point_sample, xys = sampler.get_groupby_frequency_data()
                                n_total_point["if_contain_x_categorical"] = n_total_point_sample["if_contain_x_categorical"]
                            else:
                                raise FileNotFoundError(
                                    "scaling factor should come from the " +
                                    ratio + " in the warehouse folder, as"
                                    " stated in the SQL. However, the file is not found.")
                        else:
                            n_total_point, xys = sampler.get_groupby_frequency_data()
                            # print(n_total_point)
                            # for cases when the data file is treated as a sample, we need to scale up the frequency for each group.
                            if ratio > 1:
                                file_size = sampler.n_total_point
                                ratio = float(ratio)/file_size
                            # if 0 < ratio < 1:
                            scaled_n_total_point = {}
                            if "if_contain_x_categorical" in n_total_point:
                                scaled_n_total_point["if_contain_x_categorical"] = n_total_point.pop(
                                    "if_contain_x_categorical")
                            if "categorical_distinct_values" in n_total_point:
                                scaled_n_total_point["categorical_distinct_values"] = n_total_point.pop(
                                    "categorical_distinct_values")
                            if "x_categorical_columns" in n_total_point:
                                scaled_n_total_point["x_categorical_columns"] = n_total_point.pop(
                                    "x_categorical_columns")
                            for key in n_total_point:
                                # print("key", key, n_total_point[key])

                                if not isinstance(n_total_point[key], dict):
                                    scaled_n_total_point[key] = n_total_point[key]/ratio
                                else:
                                    scaled_n_total_point[key] = {}
                                    for sub_key in n_total_point[key]:
                                        scaled_n_total_point[key][sub_key] = n_total_point[key][sub_key]/ratio
                            n_total_point = scaled_n_total_point
                            # print("scaled_n_total_point", scaled_n_total_point)

                        # no categorical x attributes
                        if not n_total_point['if_contain_x_categorical']:
                            if not self.config.get_config()["b_use_gg"]:
                                n_total_point.pop(
                                    "if_contain_x_categorical")
                                # xys.pop("if_contain_x_categorical")
                                kdeModelWrapper = KdeModelTrainer(
                                    mdl, tbl, xheader_continous[0], yheader,
                                    groupby_attribute=groupby_attribute,
                                    groupby_values=list(
                                        n_total_point.keys()),
                                    n_total_point=n_total_point,
                                    x_min_value=-np.inf, x_max_value=np.inf,
                                    config=self.config.copy()).fit_from_df(
                                    xys["data"], self.runtime_config, network_size="large")

                                qe_mdn = MdnQueryEngine(
                                    kdeModelWrapper, config=self.config.copy())
                                qe_mdn.serialize2warehouse(
                                    self.config.get_config()['warehousedir'])
                                # kdeModelWrapper.serialize2warehouse()
                                self.model_catalog.add_model_wrapper(
                                    qe_mdn)

                            else:
                                # print("n_total_point ", n_total_point)
                                queryEngineBundle = MdnQueryEngineBundle(
                                    config=self.config.copy()).fit(xys, groupby_attribute,
                                                                   n_total_point, mdl, tbl,
                                                                   xheader_continous, yheader,
                                                                   self.runtime_config)  # n_per_group=n_per_gg,n_mdn_layer_node = n_mdn_layer_node,encoding = encoding,b_grid_search = b_grid_search

                                self.model_catalog.add_model_wrapper(
                                    queryEngineBundle)
                                queryEngineBundle.serialize2warehouse(
                                    self.config.get_config()['warehousedir'])
                        else:  # x has categorical attributes
                            if not self.config.get_config()["b_use_gg"]:
                                qeXContinuous = MdnQueryEngineXCategorical(
                                    self.config.copy())
                                qeXContinuous.fit(mdl, tbl, xys, n_total_point, usecols={
                                    "y": yheader, "x_continous": xheader_continous,
                                    "x_categorical": xheader_categorical, "gb": groupby_attribute}, runtime_config=self.runtime_config
                                )  # device=device, encoding=encoding, b_grid_search=b_grid_search
                                qeXContinuous.serialize2warehouse(
                                    self.config.get_config()['warehousedir'])
                                self.model_catalog.add_model_wrapper(
                                    qeXContinuous)
                            else:
                                raise ValueError(
                                    "GoG support for categorical attributes is not supported.")
                time2 = datetime.now()
                t = (time2 - time1).seconds
                if self.runtime_config['b_show_latency']:
                    print("time cost: " + str(t) + "s.")
                print("------------------------")

                # rest config
                self.last_config = None

            elif sql_type == "dml":
                predictions = None
                # DML, provide the prediction using models
                mdl = self.parser.get_from_name()
                gb_to_print, [
                    func, yheader, distinct_condition] = self.parser.get_dml_aggregate_function_and_variable()
                if self.parser.if_where_exists():
                    start = datetime.now()
                    print("OK")
                    where_conditions = self.parser.get_dml_where_categorical_equal_and_range()
                    # xheader, x_lb, x_ub = self.parser.get_dml_where_categorical_equal_and_range()
                    model = self.model_catalog.model_catalog[mdl + '.pkl']
                    x_header_density = model.density_column

                    # print("where_conditions", where_conditions)
                    [x_lb, x_ub] = [where_conditions[2][x_header_density][i]
                                    for i in [0, 1]]
                    filter_dbest = dict(where_conditions[2])
                    filter_dbest = [filter_dbest[next(iter(filter_dbest))][i]
                                    for i in [0, 1]]

                    predictions = model.predicts(func, x_lb, x_ub, where_conditions,
                                                 runtime_config=self.runtime_config, groups=None, filter_dbest=filter_dbest)
                    # predictions = model.predict_one_pass(
                    #     func, x_lb, x_ub, n_jobs=n_jobs)

                else:
                    print(
                        "support for query without where clause is not implemented yet! abort!")

                # if not self.parser.if_contain_groupby():  # if group by is not involved in the query
                #     simple_model_wrapper = self.model_catalog.model_catalog[get_pickle_file_name(
                #         mdl)]
                #     reg = simple_model_wrapper.reg

                #     density = simple_model_wrapper.density
                #     n_sample_point = int(simple_model_wrapper.n_sample_point)
                #     n_total_point = int(simple_model_wrapper.n_total_point)
                #     x_min_value = float(simple_model_wrapper.x_min_value)
                #     x_max_value = float(simple_model_wrapper.x_max_value)
                #     query_engine = QueryEngine(reg, density, n_sample_point,
                #                                n_total_point, x_min_value, x_max_value,
                #                                self.config)
                #     p, t = query_engine.predict(func, x_lb=x_lb, x_ub=x_ub)
                #     print("OK")
                #     print(p)
                #     if self.config.get_config()['verbose']:
                #         print("time cost: " + str(t))
                #     print("------------------------")
                #     return p, t

                # else:  # if group by is involved in the query
                #     if self.config.get_config()['reg_type'] == "qreg":
                #         start = datetime.now()
                #         predictions = {}
                #         groupby_attribute = self.parser.get_groupby_value()
                #         groupby_key = mdl + "_groupby_" + groupby_attribute

                #         for group_value, model_wrapper in self.model_catalog.model_catalog[groupby_key].items():
                #             reg = model_wrapper.reg
                #             density = model_wrapper.density
                #             n_sample_point = int(model_wrapper.n_sample_point)
                #             n_total_point = int(model_wrapper.n_total_point)
                #             x_min_value = float(model_wrapper.x_min_value)
                #             x_max_value = float(model_wrapper.x_max_value)
                #             query_engine = QueryEngine(reg, density, n_sample_point, n_total_point, x_min_value,
                #                                        x_max_value,
                #                                        self.config)
                #             predictions[model_wrapper.groupby_value] = query_engine.predict(
                #                 func, x_lb=x_lb, x_ub=x_ub)[0]

                #         print("OK")
                #         for key, item in predictions.items():
                #             print(key, item)

                #     else:  # use mdn models to give the predictions.
                #         start = datetime.now()
                #         # predictions = {}
                #         groupby_attribute = self.parser.get_groupby_value()
                #         # no categorical x attributes
                #         # x_categorical_attributes, x_categorical_values, x_categorical_conditions = self.parser.get_dml_where_categorical_equal_and_range()
                #         x_categorical_conditions = self.parser.get_dml_where_categorical_equal_and_range()

                #         # no x categrical attributes, use a single model to predict.
                #         if not x_categorical_conditions[0]:
                #             if not self.config.get_config()["b_use_gg"]:
                #                 # qe_mdn = MdnQueryEngine(self.model_catalog.model_catalog[mdl + ".pkl"],
                #                 #                         self.config)
                #                 where_conditions = self.parser.get_dml_where_categorical_equal_and_range()
                #                 # xheader, x_lb, x_ub = self.parser.get_dml_where_categorical_equal_and_range()

                #                 qe_mdn = self.model_catalog.model_catalog[mdl + ".pkl"]
                #                 x_header_density = qe_mdn.density_column
                #                 [x_lb, x_ub] = [where_conditions[2][x_header_density][i]
                #                                 for i in [0, 1]]
                #                 print("OK")
                #                 predictions = qe_mdn.predict_one_pass(func, x_lb=x_lb, x_ub=x_ub,
                #                                                       n_jobs=n_jobs, )  # result2file=result2file,n_division=n_division
                #             else:
                #                 qe_mdn = self.model_catalog.model_catalog[mdl + ".pkl"]
                #                 # qe_mdn = MdnQueryEngine(qe_mdn, self.config)
                #                 print("OK")
                #                 predictions = qe_mdn.predicts(func, x_lb=x_lb, x_ub=x_ub,
                #                                               n_jobs=n_jobs, )

                #         else:
                #             pass
                #             # print("OK")
                #             # if not self.config.get_config()["b_use_gg"]:
                #             #     # print("x_categorical_values",
                #             #     #       x_categorical_values)
                #             #     # print(",".join(x_categorical_values))
                #             #     filter_dbest = self.parser.get_filter()
                #             #     self.model_catalog.model_catalog[mdl + '.pkl'].predicts(
                #             #         func, x_lb, x_ub, x_categorical_conditions,  n_jobs=1, filter_dbest=filter_dbest)  # ",".join(x_categorical_values)
                #             # else:
                #             #     pass

                if self.runtime_config['b_show_latency']:
                    end = datetime.now()
                    time_cost = (end - start).total_seconds()
                    print("Time cost: %.4fs." % time_cost)
                print("------------------------")
                return predictions

            else:  # process SET query
                if self.last_config:
                    self.config = self.last_config
                else:
                    self.config = DbestConfig()
                try:
                    key, value = self.parser.get_set_variable_value()
                    if key in self.config.get_config():
                        # check variable value before assignment
                        if key.lower() == "encoder":
                            value = value.lower()
                            if value not in ["onehot", "binary", "embedding"]:
                                value = "binary"
                                print(
                                    "encoder is not set to a proper value, use default encoding type: binary.")

                        self.config.get_config()[key] = value
                        print("OK, " + key + " is updated.")
                    else:  # if variable is within runtime_config
                        # check if "device" is set. we need to make usre when GPU is not availabe, cpu is used instead.
                        if key.lower() == "device":
                            value = value.lower()
                            if value in ["cpu", "gpu"]:
                                if torch.cuda.is_available():
                                    if value == "gpu":
                                        value = "cuda:0"
                                        try:
                                            set_start_method_torch('spawn')
                                        except RuntimeError:
                                            print("Fail to set start method as spawn for pytorch multiprocessing, " +
                                                  "use default in advance. (see queryenginemdn "
                                                  "for more info.)")
                                    else:
                                        set_start_method_cpu("spawn")
                                    if self.runtime_config["v"]:
                                        print("device is set to " + value)
                                else:
                                    if value == "gpu":
                                        print(
                                            "GPU is not available, use CPU instead")
                                        value = "cpu"
                                    if value == "cpu":
                                        if self.runtime_config["v"]:
                                            print("device is set to " + value)
                            else:
                                print("Only GPU or CPU is supported.")
                                return

                        self.runtime_config[key] = value
                        if key in self.runtime_config:
                            print("OK, " + key + " is updated.")
                        else:
                            print("OK, local variable "+key+" is defined.")
                except TypeError:
                    # self.parser.get_set_variable_value() does not return correctly
                    print("Parameter is not changed. Please check your SQL!")

                # save the config
                self.last_config = self.config

    def set_table_counts(self, dic):
        self.n_total_records = dic
