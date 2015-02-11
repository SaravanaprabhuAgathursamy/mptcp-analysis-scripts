#! /usr/bin/python
# -*- coding: utf-8 -*-
#
#  Copyright 2015 Matthieu Baerts & Quentin De Coninck
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
#  Contains code related to the processing of MPTCP traces

from __future__ import print_function

##################################################
##                   IMPORTS                    ##
##################################################

import common as co
import glob
import Gnuplot
import os
import shutil
import subprocess
import sys
import tcp
import tempfile

##################################################
##                  CONSTANTS                   ##
##################################################

# mptcptrace file identifier in csv filename for sequence number informations
MPTCP_SEQ_FNAME = '_seq_'
# mptcptrace file identifier in csv filename for subflow number informations
MPTCP_SF_FNAME = '_sf_'
# mptcptrace stats files prefix in csv filename of a subflow
MPTCP_STATS_PREFIX = 'stats_'

##################################################
##                  EXCEPTIONS                  ##
##################################################


class MPTCPTraceError(Exception):
    pass

##################################################
##           CONNECTION DATA RELATED            ##
##################################################


class MPTCPSubFlow(co.BasicFlow):
    """ Represent a MPTCP subflow """
    subflow_id = ""

    def __init__(self, sid):
        super(MPTCPSubFlow, self).__init__()
        self.subflow_id = sid


class MPTCPConnection(co.BasicConnection):
    """ Represent a MPTCP connection """
    flows = {}

    def __init__(self, cid):
        super(MPTCPConnection, self).__init__(cid)
        self.flows = {}


def extract_flow_data(out_file):
    """ Given an (open) file, return a dictionary of as many elements as there are mptcp flows """
    # Return at the beginning of the file
    out_file.seek(0)
    data = out_file.readlines()
    connections = {}
    current_connection = False
    for line in data:
        # Case 1: line start with MPTCP connection
        if line.startswith("MPTCP connection"):
            # A typical line: MPTCP connection 0 with id 2
            words = line.split()
            current_connection = words[-1]
            connections[current_connection] = MPTCPConnection(current_connection)

        # Case 2: line for a subflow
        elif current_connection is not False and line.startswith("\tSubflow"):
            # A typical line:
            #   Subflow 0 with wscale : 6 0 IPv4 sport 59570 dport 443 saddr
            # 37.185.171.74 daddr 194.78.99.114
            words = line.split()
            sub_flow_id = words[1]
            subflow = MPTCPSubFlow(sub_flow_id)
            index_wscale = words.index("wscale")
            subflow.attr[
                co.WSCALESRC] = words[index_wscale + 2]
            subflow.attr[
                co.WSCALEDST] = words[index_wscale + 3]
            subflow.attr[
                co.TYPE] = words[index_wscale + 4]
            index = words.index("sport")
            while index + 1 < len(words):
                attri = words[index]
                value = words[index + 1]
                subflow.attr[attri] = value
                index += 2

            subflow.indicates_wifi_or_rmnet()
            connections[current_connection].flows[sub_flow_id] = subflow

        # Case 3: skip the line (no more current connection)
        else:
            current_connection = False
    return connections

##################################################
##        CONNECTION IDENTIFIER RELATED         ##
##################################################


def get_connection_id(csv_fname):
    """ Given the filename of the csv file, return the id of the MPTCP connection
        The id (returned as str) is assumed to be between last _ and last . in csv_fname
    """
    last_underscore_index = csv_fname.rindex("_")
    last_dot_index = csv_fname.rindex(".")
    return csv_fname[last_underscore_index + 1:last_dot_index]


def is_reverse_connection(csv_fname):
    """ Given the filename of the csv file, return True is it is a c2s flow or False if it is a s2c
        one
        The type is assumed to be before the first _ in csv_fname
    """
    first_underscore_index = csv_fname.index("_")
    return (csv_fname[0:first_underscore_index] == "s2c")


##################################################
##                  MPTCPTRACE                  ##
##################################################


def process_mptcptrace_cmd(cmd, pcap_fname):
    """ Launch the command cmd given in argument, and return a dictionary containing information
        about connections of the pcap file analyzed
        Raise a MPTCPTraceError if mptcptrace encounters problems
    """
    pcap_flow_data = pcap_fname[:-5] + '.out'
    flow_data_file = open(pcap_flow_data, 'w+')
    if subprocess.call(cmd, stdout=flow_data_file) != 0:
        raise MPTCPTraceError("Error of mptcptrace with " + pcap_fname)

    connections = extract_flow_data(flow_data_file)
    # Don't forget to close and remove pcap_flow_data
    flow_data_file.close()
    os.remove(pcap_flow_data)
    return connections


##################################################
##                GRAPH RELATED                 ##
##################################################


def interesting_graph(csv_fname, connections):
    """ Return True if the MPTCP graph is worthy, else False
        This function assumes that a graph is interesting if it has at least one connection that
        if not 127.0.0.1 -> 127.0.0.1
        Note that is the graph is interesting and IPv4, indicates if the traffic is Wi-Fi or rmnet
    """
    connection_id = get_connection_id(csv_fname)
    for sub_flow_id, conn in connections[connection_id].flows.iteritems():
        # Only had the case for IPv4, but what is its equivalent in IPv6?
        if not conn.attr[co.TYPE] == 'IPv4':
                return True
        if not (conn.attr[co.SADDR] == co.LOCALHOST_IPv4 and conn.attr[co.DADDR] == co.LOCALHOST_IPv4):
                return True
    return False


def get_begin_values(first_line):
    split_line = first_line.split(',')
    return float(split_line[0]), int(split_line[1])


def get_data_csv(csv_graph_tmp_dir, csv_fname, data, begin_time, begin_seq, connections, conn_id, is_reversed):
    """ Return a list of lists of data
        Index 0: data of tsg of flow 0
        Index 1: data of tsg of flow 1
        ...
        Index 4: data of reinjected segment that was first on flow 0
        Index 5: data of reinjected segment that was first on flow 1
    """
    graph_data = [[], [], [], []]
    reinject_data = [[], [], [], []]
    acks_data = [[], [], [], []]
    last_offset = 0
    offsets = {0: 0, 1:0, 2:0, 3:0}
    acks_offsets = {0: 0, 1:0, 2:0, 3:0}
    last_acks_offset = 0
    last_time = 0.0

    for line in data:
        split_line = line.split(',')
        time = float(split_line[0]) - begin_time
        seq = int(split_line[1]) - begin_seq
        if int(split_line[3]) == 0:
            # Ack
            seq_to_plot = seq - last_acks_offset + acks_offsets[int(split_line[2]) - 1]
            acks_data[int(split_line[2])].append([time, seq_to_plot])
            acks_offsets[int(split_line[2]) - 1] = seq_to_plot
            last_acks_offset = seq
        elif int(split_line[3]) == 1:
            # Map
            seq_to_plot = seq - last_offset + offsets[int(split_line[2]) - 1]
            if int(split_line[5]) == -1:
                # Not already seen on another flow
                graph_data[int(split_line[2]) - 1].append([time, seq_to_plot])
            else:
                # Reinjected segment
                graph_data[int(split_line[2]) - 1].append([time, seq_to_plot])
                reinject_data[int(split_line[5]) - 1].append([time, seq_to_plot])

            offsets[int(split_line[2]) - 1] = seq_to_plot
            last_offset = seq
            last_time = time

    for i in range(0, len(graph_data)):
        graph_data[i].append([last_time, offsets[i]])

    for i in range(0, len(connections[conn_id].flows)):
        if is_reversed:
            connections[conn_id].flows[str(i)].attr[co.REINJ_ORIG_PACKS_D2S] = len(reinject_data[i])
        else:
            connections[conn_id].flows[str(i)].attr[co.REINJ_ORIG_PACKS_S2D] = len(reinject_data[i])

    return graph_data + reinject_data, acks_data


def generate_title(csv_fname, connections):
    """ Generate the title for a mptcp connection """

    connection_id = get_connection_id(csv_fname)
    title = "flows:" + str(len(connections[connection_id].flows)) + " "

    # If not reverse, correct order, otherwise reverse src and dst
    reverse = is_reverse_connection(csv_fname)

    # Show all details of the subflows
    for sub_flow_id, conn in connections[connection_id].flows.iteritems():
        # \n must be interpreted as a raw type to works with GnuPlot.py
        title += '\n' + "sf: " + sub_flow_id + " "
        if reverse:
            title += "(" + conn.attr[co.WSCALEDST] + " " + conn.attr[co.WSCALESRC] + ") "
            title += conn.attr[co.DADDR] + ":" + conn.attr[co.DPORT] + \
                " -> " + conn.attr[co.SADDR] + ":" + conn.attr[co.SPORT]
        else:
            title += "(" + conn.attr[co.WSCALESRC] + " " + conn.attr[co.WSCALEDST] + ") "
            title += conn.attr[co.SADDR] + ":" + conn.attr[co.SPORT] + \
                " -> " + conn.attr[co.DADDR] + ":" + conn.attr[co.DPORT]
        if co.IF in conn.attr:
            title += " [" + conn.attr[co.IF] + "]"
    return title


def create_graph_csv(data_plot, acks_plot, pcap_fname, csv_fname, graph_dir_exp, connections):
    """ Generate pdf for the csv file of the pcap file, if interesting
    """
    # First see if useful to show the graph
    if not interesting_graph(csv_fname, connections):
        return
    # try:
    #     csv_file = open(csv_fname)
    #     data = csv_file.readlines()
    # except IOError:
    #     print('IOError for ' + csv_fname + ': skipped', file=sys.stderr)
    #     return
    #
    # # If file was generated, the csv is not empty
    # data_split = map(lambda x: x.split(','), data)
    # data_plot = map(lambda x: map(lambda y: float(y), x), data_split)

    # g = Gnuplot.Gnuplot(debug=0)
    # g('set title "' + generate_title(csv_fname, connections) + '"')
    # g('set style data linespoints')
    # g.xlabel('Time [s]')
    # g.ylabel('Sequence number')
    # g.plot(data_plot, 'lt rgb blue')

    tsg_thgpt_dir = os.path.join(graph_dir_exp, co.TSG_THGPT_DIR)
    co.check_directory_exists(tsg_thgpt_dir)
    pdf_fname = os.path.join(tsg_thgpt_dir,
                             os.path.basename(pcap_fname)[:-5] + "_" + csv_fname[:-4] + '.pdf')
    # g.hardcopy(filename=pdf_fname, terminal='pdf')
    # g.reset()

    co.plot_line_graph(data_plot, ['0', '1', '2', '3', 'rf0', 'rf1', 'rf2', 'rf3'], ['r', 'b', 'g', 'k', 'r+', 'b+', 'g+', 'k+'], 'Time [s]', 'Sequence number [Bytes]', generate_title(csv_fname, connections), pdf_fname, titlesize=10)

    pdf_fname = os.path.join(tsg_thgpt_dir,
                             os.path.basename(pcap_fname)[:-5] + "_" + csv_fname[:-4] + "_acks" + '.pdf')
    co.plot_line_graph(data_plot, ['0', '1', '2', '3'], ['r', 'b', 'g', 'k'], 'Time [s]', 'Sequence number [Bytes]', generate_title(csv_fname, connections), pdf_fname, titlesize=10)



##################################################
##               MPTCP PROCESSING               ##
##################################################


def process_stats_csv(csv_fname, connections):
    """ Add information in connections based on the stats csv file, and remove it """
    try:
        csv_file = open(csv_fname)
        conn_id = get_connection_id(csv_fname) # Or reuse conn_id from the stats file
        data = csv_file.readlines()
        first_seqs = None
        last_acks = None
        con_time = None
        for line in data:
            if 'firstSeq' in line:
                first_seqs = line.split(';')[-2:]
            elif 'lastAck' in line:
                last_acks = line.split(';')[-2:]
            elif 'conTime' in line:
                # Only takes one of the values, because they are the same
                con_time = line.split(';')[-1]

        if first_seqs and last_acks:
            connections[conn_id].attr[co.BYTES_S2D] = int(last_acks[1]) - int(first_seqs[0])
            connections[conn_id].attr[co.BYTES_D2S] = int(last_acks[0]) - int(first_seqs[1])
        if con_time:
            connections[conn_id].attr[co.DURATION] = float(con_time)

        csv_file.close()

        # Remove now stats files
        os.remove(csv_fname)
    except IOError:
        print('IOError for ' + csv_fname + ': skipped', file=sys.stderr)
        return
    except ValueError:
        print('ValueError for ' + csv_fname + ': skipped', file=sys.stderr)
        return


def first_pass_on_seq_csv(csv_fname, relative_start):
    """ Return the smallest timestamp between the smallest one in csv_fname and relative_start"""
    minimum = relative_start
    try:
        csv_file = open(csv_fname)
        data = csv_file.readlines()
        if not data == [] and len(data) > 1:
            try:
                begin_time, begin_seq = get_begin_values(data[0])
                if begin_time < relative_start and not begin_time == 0.0:
                    minimum = begin_time
            except ValueError:
                print('ValueError for ' + csv_fname + ': keep old value', file=sys.stderr)

        csv_file.close()
    except IOError:
        print('IOError for ' + csv_fname + ': keep old value', file=sys.stderr)

    return minimum

def first_pass_on_csvs(connections):
    """ Do a first pass on csvs in current directory, without modifying them
        This returns the relative start of all connections and modify connections to add information
        contained in the csvs
    """
    relative_start = float("inf")
    for csv_fname in glob.glob('*.csv'):
        if csv_fname.startswith(MPTCP_STATS_PREFIX):
            process_stats_csv(csv_fname, connections)

        elif MPTCP_SEQ_FNAME in csv_fname:
            relative_start = first_pass_on_seq_csv(csv_fname, relative_start)

    return relative_start


def process_seq_csv(csv_fname, csv_graph_tmp_dir, connections, relative_start, min_bytes):
    """ If the csv is interesting, rewrite it in another folder csv_graph_tmp_dir
        Delete the csv given in argument
    """
    graph_data, acks_data = None, None
    try:
        conn_id = get_connection_id(csv_fname)
        is_reversed = is_reverse_connection(csv_fname)
        csv_file = open(csv_fname)
        data = csv_file.readlines()
        # Check if there is data in file (and not only one line of 0s)
        if not data == [] and len(data) > 1:
            if ((is_reversed and connections[conn_id].attr[co.BYTES_D2S] >= min_bytes) or
                (not is_reversed and connections[conn_id].attr[co.BYTES_S2D] >= min_bytes)):
                # Collect begin time and seq num to plot graph starting at 0
                try:
                    begin_time, begin_seq = get_begin_values(data[0])
                    graph_data, acks_data = get_data_csv(csv_graph_tmp_dir, csv_fname, data, relative_start, begin_seq, connections, conn_id, is_reversed)
                except ValueError:
                    print('ValueError for ' + csv_fname + ': skipped', file=sys.stderr)

        csv_file.close()
        # Remove the csv file
        os.remove(csv_fname)

        return graph_data, acks_data

    except IOError:
        print('IOError for ' + csv_fname + ': skipped', file=sys.stderr)
        return


def plot_congestion_graphs(pcap_fname, graph_dir_exp, connections):
    """ Given MPTCPConnections (in connections), plot their congestion graph """
    cwin_graph_dir = os.path.join(graph_dir_exp, co.CWIN_DIR)
    co.check_directory_exists(cwin_graph_dir)

    formatting = ['b', 'r', 'g', 'p']

    for conn_id, conn in connections.iteritems():
        base_graph_fname = os.path.basename(pcap_fname[:-5]) + '_' + conn.conn_id + '_cwin'

        for direction, data_if in conn.attr[co.CWIN_DATA].iteritems():
            dir_abr = 'd2s' if direction == co.D2S else 's2d' if direction == co.S2D else '?'
            graph_fname = base_graph_fname + '_' + dir_abr
            graph_fname += '.pdf'
            graph_fname = os.path.join(cwin_graph_dir, graph_fname)

            nb_curves = len(data_if)
            co.plot_line_graph(data_if.values(), data_if.keys(), formatting[:nb_curves], "Time [s]", "Congestion window [Bytes]", "Congestion window", graph_fname, ymin=0)


# We can't change dir per thread, we should use processes
def process_trace(pcap_fname, graph_dir_exp, stat_dir_exp, aggl_dir_exp, min_bytes=0):
    """ Process a mptcp pcap file and generate graphs of its subflows """
    csv_tmp_dir = tempfile.mkdtemp(dir=os.getcwd())
    connections = None
    try:
        with co.cd(csv_tmp_dir):
            # If segmentation faults, remove the -S option
            cmd = ['mptcptrace', '-f', pcap_fname, '-s', '-S', '-w', '2']
            connections = process_mptcptrace_cmd(cmd, pcap_fname)

            csv_graph_tmp_dir = tempfile.mkdtemp(dir=graph_dir_exp)
            # The mptcptrace call will generate .csv files to cope with

            # First see all csv files, to detect the relative 0 of all connections
            # Also, compute the duration and number of bytes of the MPTCP connection
            relative_start = first_pass_on_csvs(connections)

            # Then really process csv files
            for csv_fname in glob.glob('*.csv'):
                if MPTCP_SEQ_FNAME in csv_fname:
                    graph_data, acks_data = process_seq_csv(csv_fname, csv_graph_tmp_dir, connections, relative_start, min_bytes)
                    create_graph_csv(graph_data, acks_data, pcap_fname, csv_fname, graph_dir_exp, connections)


            # with co.cd(csv_graph_tmp_dir):
            #     for csv_fname in glob.glob('*.csv'):
            #         # No point to plot information on subflows (as many points as there are subflows)
            #         if MPTCP_SF_FNAME not in csv_fname:
            #             create_graph_csv(pcap_fname, csv_fname, graph_dir_exp, connections)
            #         # Remove the csv file
            #         os.remove(csv_fname)

            # Remove temp dirs
            shutil.rmtree(csv_graph_tmp_dir)
    except MPTCPTraceError as e:
        print(str(e) + "; skip mptcp process", file=sys.stderr)

    shutil.rmtree(csv_tmp_dir)

    # Create aggregated graphes and add per interface information on MPTCPConnection
    # This will save the mptcp connections
    if connections:
        tcp.process_trace(pcap_fname, graph_dir_exp, stat_dir_exp, aggl_dir_exp, mptcp_connections=connections)
        plot_congestion_graphs(pcap_fname, graph_dir_exp, connections)
