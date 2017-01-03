#!/usr/bin/env python
# -*- coding: utf-8 -*- #
__author__ = 'Vonng (fengruohang@outlook.com)'

import json
import requests
import happybase
from multicorn import ForeignDataWrapper
from multicorn.utils import log_to_postgres as log
from multicorn import ColumnDefinition, Qual


def pprint(obj):
    log(json.dumps(obj, indent=4))


class HappyBaseFdw(ForeignDataWrapper):
    def __init__(self, fdw_options, fdw_columns):
        super(HappyBaseFdw, self).__init__(fdw_options, fdw_columns)
        log("-- Init FDW ------------------------------")
        self.fdw_columns = fdw_columns
        self.fdw_options = fdw_options

        self.mode = fdw_options.get('mode', 'dev')
        self.host = fdw_options.get('host')
        self.debug = fdw_options.get('debug', None)
        self.table_name = fdw_options.get('table')
        self.prefix = fdw_options.get('prefix')

        if not self.table_name or not self.host:
            raise ValueError('host and table should be specified!')

        self.qualifier = {}
        for col_name, col_def in fdw_columns.iteritems():
            qualifier = col_def.options.get('qualifier')
            if not qualifier:
                if self.prefix:
                    qualifier = self.prefix + col_name.split('_', 1)[-1]
                else:
                    qualifier = col_name.replace('_', ':', 1)
            self.qualifier[col_name] = qualifier

        if self.debug:
            log("-- Columns ------------------------------")
            log(fdw_options)
            for col_name, cd in fdw_columns.iteritems():
                log("%-12s\t[%4d :%-30s(%s:%s)] Opt:%s" % (
                    cd.column_name, cd.type_oid, cd.type_name, cd.base_type_name, cd.typmod, cd.options))
            log(self.qualifier)

        self.conn = happybase.Connection(self.host)
        self.table = self.conn.table(self.table_name)

    def execute(self, quals, columns, sortkeys=None):
        log("-- Exec begin ------------------------------")

        if self.debug:
            log("-- Cols & Quals ------------------------------")
            log(columns)
            log(quals)
            for qual in quals:
                log("%s %s %s" % (qual.field_name, qual.operator, qual.value))

        # Build rowkey: type of rowkey could be str, list, dict
        rowkey = None
        for qual in quals:
            if qual.field_name == 'rowkey':
                if qual.operator == '=':
                    rowkey = qual.value
                elif qual.is_list_operator:
                    rowkey = qual.value
                elif qual.operator == '<=':
                    if isinstance(rowkey, dict):
                        rowkey['until'] = qual.value.encode('utf-8')
                    else:
                        rowkey = {'until': qual.value.encode('utf-8')}
                elif qual.operator == '>=':
                    if isinstance(rowkey, dict):
                        rowkey['since'] = qual.value
                    else:
                        rowkey = {'since': qual.value}
                else:
                    log(qual)
                    raise ValueError("Supported operators on rowkey : =,<=,>=,in,any,between")

        # Build columns
        qualifiers = [self.qualifier[k] for k in columns if k != 'rowkey']

        log(qualifiers)

        if isinstance(rowkey, basestring):  # Single rowkey
            response = self.table.row(rowkey, qualifiers)
            if not response:
                yield {"rowkey": rowkey}
            else:
                buf = {col_name: response.get(qualifier) for col_name, qualifier in self.qualifier.iteritems()}
                buf["rowkey"] = rowkey
                yield buf


        elif isinstance(rowkey, list):  # multiple rowkey
            responses = self.table.rows(rowkey, qualifiers)
            log(responses)
            if not responses:
                for rk in rowkey:
                    yield {"rowkey": rk}
            else:
                for rk, response in responses:
                    buf = {col_name: response.get(qualifier) for col_name, qualifier in self.qualifier.iteritems()}
                    buf["rowkey"] = rk
                    yield buf

        elif isinstance(rowkey, dict):  # Range rowkey
            for rk, response in self.table.scan(rowkey.get('since'), rowkey.get('until'), columns=qualifiers):
                buf = {col_name: response.get(qualifier) for col_name, qualifier in self.qualifier.iteritems()}
                buf["rowkey"] = rk
                yield buf

        else:
            raise ValueError('Invalid rowkey')
