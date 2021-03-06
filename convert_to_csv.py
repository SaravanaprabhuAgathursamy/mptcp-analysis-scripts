#! /usr/bin/python
# -*- coding: utf-8 -*-
#
#  Copyright 2015 Quentin De Coninck
#
#  This program is free software; you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation; either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software
#  Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#  MA 02110-1301, USA.
#
#  To install on this machine: matplotlib, numpy

from __future__ import print_function

from datetime import timedelta

import argparse
import matplotlib
# Do not use any X11 backend
matplotlib.use('Agg')
matplotlib.rcParams['pdf.fonttype'] = 42
matplotlib.rcParams['ps.fonttype'] = 42
import matplotlib.pyplot as plt
import numpy as np
import os
import pickle
import sys

import common as co
import mptcp
import tcp

DEF_CSV = 'csv'

##################################################
#                   ARGUMENTS                    #
##################################################

parser = argparse.ArgumentParser(
    description="Summarize stat files generated by analyze")
parser.add_argument("-s",
                    "--stat", help="directory where the stat files are stored", default=co.DEF_STAT_DIR + '_' + co.DEF_IFACE)
parser.add_argument('-c',
                    "--csv", help="directory where the csv will be stored", default=DEF_CSV)

args = parser.parse_args()
stat_dir_exp = os.path.abspath(os.path.expanduser(args.stat))
csv_dir_exp = os.path.abspath(os.path.expanduser(args.csv))
co.check_directory_exists(csv_dir_exp)

##################################################
#                  GET THE DATA                  #
##################################################


def ensures_smartphone_to_proxy(connections):
    for conn_id in connections.keys():
        if isinstance(connections[conn_id], mptcp.MPTCPConnection):
            inside = True
            for flow_id, flow in connections[conn_id].flows.iteritems():
                if not [x for x in co.PREFIX_IP_PROXY if flow.attr[co.DADDR].startswith(x)] and not flow.attr[co.DADDR] in co.IP_PROXY:
                    connections.pop(conn_id, None)
                    inside = False
                    break
            if inside:
                for direction in co.DIRECTIONS:
                    # This is a fix for wrapping seq num
                    if connections[conn_id].attr[direction].get(co.BYTES_MPTCPTRACE, -2 ** 32) < -1:
                        connections[conn_id].attr[direction][co.BYTES_MPTCPTRACE] = 2 ** 32 + connections[conn_id].attr[direction].get(co.BYTES_MPTCPTRACE, -2 ** 32)


def short_direction(direction):
    return 'c2s' if direction == co.C2S else 's2c' if direction == co.S2C else '?'


IS_C2S = 'is_c2s'
POSITION = 'position'
MPTCP_CONNECTIONS = 'mptcp_conns_'
MPTCP_CONNECTIONS_ONE2ONE_FNAME = 'mptcp_conns_o2o_'
MPTCP_CONNECTIONS_MANY2ONE_FNAME = 'mptcp_conns_m2o_'
MPTCP_SUBFLOWS_ONE2ONE_FNAME = 'mptcp_sfs_o2o_'
MPTCP_SUBFLOWS_MANY2ONE_FNAME = 'mptcp_sfs_m2o_'
MPTCP_CONNECTION_FNAME_FIELD = 'fname'
MPTCP_CONNECTION_ID_FIELD = co.CONN_ID
MPTCP_SUBFLOW_ID_FIELD = 'flow_id'
MPTCP_CONNECTIONS_ONE2ONE_SINGLE_FIELDS = [co.DURATION, co.SOCKS_DADDR, co.SOCKS_PORT, co.START]
MPTCP_CONNECTIONS_ONE2ONE_DIRECTION_FIELDS = [co.BYTES_MPTCPTRACE, co.REINJ_BYTES, co.REINJ_PC, co.RTT_SAMPLES, co.RTT_AVG, co.RTT_STDEV, co.RTT_MIN,
                                              co.RTT_25P, co.RTT_MED, co.RTT_75P, co.RTT_90P, co.RTT_95P, co.RTT_97P, co.RTT_98P, co.RTT_99P,
                                              co.RTT_MAX]
MPTCP_CONNECTIONS_MANY2ONE_DIRECTION_FIELDS = [co.BURSTS]
MPTCP_CONNECTIONS_MANY2ONE_DIRECTION_SUBFIELDS = {co.BURSTS: ['flow_id', 'bytes_seq', 'packets_seq', 'duration_seq', 'start_seq']}
MPTCP_SUBFLOWS_ONE2ONE_SINGLE_FIELDS = [co.SADDR, co.SPORT, co.DADDR, co.DPORT, co.SOCKS_DADDR, co.SOCKS_PORT, co.DURATION, co.START, co.TYPE, co.IF,
                                        co.WSCALESRC, co.WSCALEDST]
MPTCP_SUBFLOWS_ONE2ONE_DIRECTION_FIELDS = [co.BYTES, co.BYTES_DATA, co.BYTES_RETRANS, co.CWIN_MAX, co.CWIN_MIN, co.NB_ACK, co.NB_FIN,
                                           co.NB_FLOW_CONTROL, co.NB_NET_DUP, co.NB_REORDERING, co.NB_RST, co.NB_RTX_FR, co.NB_RTX_RTO, co.NB_SYN,
                                           co.NB_UNKNOWN, co.NB_UNNECE_RTX_FR, co.NB_UNNECE_RTX_RTO, co.PACKS, co.PACKS_OOO, co.PACKS_RETRANS,
                                           co.REINJ_ORIG_BYTES, co.REINJ_ORIG_PACKS, co.RTT_AVG, co.RTT_MAX, co.RTT_MIN, co.RTT_SAMPLES,
                                           co.RTT_STDEV, co.SS_MIN, co.SS_MAX, co.TIME_FIRST_ACK, co.TIME_FIRST_PAYLD, co.TIME_LAST_ACK_TCP,
                                           co.TIME_LAST_PAYLD, co.TIME_LAST_PAYLD_TCP, co.TIME_LAST_PAYLD_WITH_RETRANS_TCP, co.TIME_FIN_ACK_TCP, co.TTL_MAX, co.TTL_MIN]
MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_FIELDS = [co.IS_REINJ, co.REINJ_ORIG, co.REINJ_ORIG_TIMESTAMP, co.TIMESTAMP_RETRANS]
MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_SUBFIELDS = {co.IS_REINJ: {'timestamp': 'bytes'}, co.REINJ_ORIG: {'range_bytes': 'nb_reinjected'},
                                               co.REINJ_ORIG_TIMESTAMP: 'reinjection_orig_timestamp',
                                               co.TIMESTAMP_RETRANS: ['timestamp_retransmission', 'delta_with_first_time_sent', 'delta_with_last_time_sent', 'delta_with_last_sent']}


def make_header_line_mptcp_connections_one2one_fields(conns_o2o_file):
    conns_o2o_file.write(MPTCP_CONNECTION_FNAME_FIELD + ";" + MPTCP_CONNECTION_ID_FIELD)
    for field_name in MPTCP_CONNECTIONS_ONE2ONE_SINGLE_FIELDS:
        conns_o2o_file.write(";" + str(field_name))

    for direction in co.DIRECTIONS:
        short_dir = short_direction(direction) + "_"
        for field_name in MPTCP_CONNECTIONS_ONE2ONE_DIRECTION_FIELDS:
            conns_o2o_file.write(";" + short_dir + str(field_name))

    conns_o2o_file.write("\n")


def make_data_lines_mptcp_connections_one2one_fields(fbasename, connections, conns_o2o_file):
    sorted_conn_ids = sorted(connections.keys())
    for conn_id in sorted_conn_ids:
        conn = connections[conn_id]
        conns_o2o_file.write(fbasename + ";" + str(conn_id))
        for field_name in MPTCP_CONNECTIONS_ONE2ONE_SINGLE_FIELDS:
            if field_name == co.START:
                conns_o2o_file.write(";" + str(conn.attr.get(field_name, timedelta()).total_seconds()))
            else:
                conns_o2o_file.write(";" + str(conn.attr.get(field_name, "NULL")))

        for direction in co.DIRECTIONS:
            for field_name in MPTCP_CONNECTIONS_ONE2ONE_DIRECTION_FIELDS:
                conns_o2o_file.write(";" + str(conn.attr[direction].get(field_name, "NULL")))

        conns_o2o_file.write("\n")


def mptcp_connections_one2one_fields(fbasename, connections):
    conns_o2o_file = open(os.path.join(csv_dir_exp, MPTCP_CONNECTIONS_ONE2ONE_FNAME + fbasename), 'w')
    make_header_line_mptcp_connections_one2one_fields(conns_o2o_file)
    make_data_lines_mptcp_connections_one2one_fields(fbasename, connections, conns_o2o_file)
    conns_o2o_file.close()


def make_header_line_mptcp_connections_many2one_direction_fields(conns_m2o_file, field_name):
    conns_m2o_file.write(MPTCP_CONNECTION_FNAME_FIELD + ";" + MPTCP_CONNECTION_ID_FIELD + ";" + IS_C2S + ";" + POSITION)
    for subfield_name in MPTCP_CONNECTIONS_MANY2ONE_DIRECTION_SUBFIELDS[field_name]:
        conns_m2o_file.write(";" + str(subfield_name))

    conns_m2o_file.write("\n")


def make_data_lines_mptcp_connections_many2one_direction_fields(fbasename, connections, conns_m2o_file, field_name):
    sorted_conn_ids = sorted(connections.keys())
    for conn_id in sorted_conn_ids:
        conn = connections[conn_id]
        for direction in co.DIRECTIONS:
            is_c2s = 1 if direction == co.C2S else 0
            data = conn.attr[direction].get(field_name, [])
            if len(data) > 0:
                pos = 0
                for elem in data:
                    conns_m2o_file.write(fbasename + ";" + str(conn_id) + ";" + str(is_c2s) + ";" + str(pos))
                    if isinstance(MPTCP_CONNECTIONS_MANY2ONE_DIRECTION_SUBFIELDS[field_name], list):
                        for subelem in elem:
                            conns_m2o_file.write(";" + str(subelem))

                    else:
                        conns_m2o_file.write(";" + str(elem))

                    conns_m2o_file.write("\n")
                    pos += 1


def mptcp_connections_many2one_fields(fbasename, connections):
    for field_name in MPTCP_CONNECTIONS_MANY2ONE_DIRECTION_FIELDS:
        conns_m2o_file = open(os.path.join(csv_dir_exp, MPTCP_CONNECTIONS_MANY2ONE_FNAME + field_name + "_" + fbasename), 'w')
        make_header_line_mptcp_connections_many2one_direction_fields(conns_m2o_file, field_name)
        make_data_lines_mptcp_connections_many2one_direction_fields(fbasename, connections, conns_m2o_file, field_name)
        conns_m2o_file.close()


def make_header_line_mptcp_subflows_one2one_fields(sfs_o2o_file):
    sfs_o2o_file.write(MPTCP_CONNECTION_FNAME_FIELD + ";" + MPTCP_CONNECTION_ID_FIELD + ";" + MPTCP_SUBFLOW_ID_FIELD)
    for field_name in MPTCP_SUBFLOWS_ONE2ONE_SINGLE_FIELDS:
        sfs_o2o_file.write(";" + str(field_name))

    for direction in co.DIRECTIONS:
        short_dir = short_direction(direction) + "_"
        for field_name in MPTCP_SUBFLOWS_ONE2ONE_DIRECTION_FIELDS:
            sfs_o2o_file.write(";" + short_dir + str(field_name))

    sfs_o2o_file.write("\n")


def make_data_lines_mptcp_subflows_one2one_fields(fbasename, connections, sfs_o2o_file):
    sorted_conn_ids = sorted(connections.keys())
    for conn_id in sorted_conn_ids:
        conn = connections[conn_id]
        sorted_flow_ids = sorted(conn.flows.keys())
        for flow_id in sorted_flow_ids:
            sfs_o2o_file.write(fbasename + ";" + str(conn_id) + ";" + str(flow_id))
            for field_name in MPTCP_SUBFLOWS_ONE2ONE_SINGLE_FIELDS:
                if field_name in [co.START, co.TIME_LAST_ACK_TCP, co.TIME_LAST_PAYLD_TCP, co.TIME_LAST_PAYLD_WITH_RETRANS_TCP, co.TIME_FIN_ACK_TCP]:
                    sfs_o2o_file.write(";" + str(conn.flows[flow_id].attr.get(field_name, timedelta()).total_seconds()))
                else:
                    sfs_o2o_file.write(";" + str(conn.flows[flow_id].attr.get(field_name, "NULL")))

            for direction in co.DIRECTIONS:
                for field_name in MPTCP_SUBFLOWS_ONE2ONE_DIRECTION_FIELDS:
                    sfs_o2o_file.write(";" + str(conn.flows[flow_id].attr[direction].get(field_name, "NULL")))

            sfs_o2o_file.write("\n")


def mptcp_subflows_one2one_fields(fbasename, connections):
    sfs_o2o_file = open(os.path.join(csv_dir_exp, MPTCP_SUBFLOWS_ONE2ONE_FNAME + fbasename), 'w')
    make_header_line_mptcp_subflows_one2one_fields(sfs_o2o_file)
    make_data_lines_mptcp_subflows_one2one_fields(fbasename, connections, sfs_o2o_file)
    sfs_o2o_file.close()


def make_header_line_mptcp_subflows_many2one_direction_fields(sfs_m2o_file, field_name):
    sfs_m2o_file.write(MPTCP_CONNECTION_FNAME_FIELD + ";" + MPTCP_CONNECTION_ID_FIELD + ";" + MPTCP_SUBFLOW_ID_FIELD + ";" + IS_C2S + ";" + POSITION)
    if isinstance(MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_SUBFIELDS[field_name], list):
        for subfield_name in MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_SUBFIELDS[field_name]:
            sfs_m2o_file.write(";" + str(subfield_name))
    elif isinstance(MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_SUBFIELDS[field_name], dict):
        for key, value in MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_SUBFIELDS[field_name].iteritems():
            sfs_m2o_file.write(";" + str(key) + ";" + str(value))

    sfs_m2o_file.write("\n")


def make_data_lines_mptcp_subflows_many2one_direction_fields(fbasename, connections, sfs_m2o_file, field_name):
    sorted_conn_ids = sorted(connections.keys())
    for conn_id in sorted_conn_ids:
        conn = connections[conn_id]
        for direction in co.DIRECTIONS:
            is_c2s = 1 if direction == co.C2S else 0
            sorted_flow_ids = sorted(conn.flows.keys())
            for flow_id in sorted_flow_ids:
                data = conn.flows[flow_id].attr[direction].get(field_name, [])
                if len(data) > 0:
                    pos = 0

                    for elem in data:
                        sfs_m2o_file.write(fbasename + ";" + str(conn_id) + ";" + str(is_c2s) + ";" + str(pos))
                        if isinstance(MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_SUBFIELDS[field_name], list):
                            if field_name == co.TIMESTAMP_RETRANS:
                                for subelem in elem:
                                    sfs_m2o_file.write(";" + str(subelem.total_seconds()))
                            else:
                                for subelem in elem:
                                    sfs_m2o_file.write(";" + str(subelem))

                        elif isinstance(MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_SUBFIELDS[field_name], dict):
                            sfs_m2o_file.write(";" + str(elem) + ";" + str(data[elem]))
                        else:
                            sfs_m2o_file.write(";" + str(elem))

                        sfs_m2o_file.write("\n")
                        pos += 1


def mptcp_subflows_many2one_fields(fbasename, connections):
    for field_name in MPTCP_SUBFLOWS_MANY2ONE_DIRECTION_FIELDS:
        sfs_m2o_file = open(os.path.join(csv_dir_exp, MPTCP_SUBFLOWS_MANY2ONE_FNAME + field_name + "_" + fbasename), 'w')
        make_header_line_mptcp_subflows_many2one_direction_fields(sfs_m2o_file, field_name)
        make_data_lines_mptcp_subflows_many2one_direction_fields(fbasename, connections, sfs_m2o_file, field_name)
        sfs_m2o_file.close()


def convert_to_csv(fname, connections):
    fbasename = os.path.splitext(os.path.basename(fname))[0]
    mptcp_connections_one2one_fields(fbasename, connections)
    mptcp_connections_many2one_fields(fbasename, connections)
    mptcp_subflows_one2one_fields(fbasename, connections)
    mptcp_subflows_many2one_fields(fbasename, connections)


for dirpath, dirnames, filenames in os.walk(stat_dir_exp):
    for fname in filenames:
        try:
            stat_file = open(os.path.join(dirpath, fname), 'r')
            connections = pickle.load(stat_file)
            stat_file.close()
            ensures_smartphone_to_proxy(connections)
            convert_to_csv(fname, connections)

        except IOError as e:
            print(str(e) + ': skip stat file ' + fname, file=sys.stderr)
