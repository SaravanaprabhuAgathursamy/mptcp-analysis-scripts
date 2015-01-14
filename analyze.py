#! /usr/bin/python
# -*- coding: utf-8 -*-
#
#  Copyright 2014-2015 Matthieu Baerts & Quentin De Coninck
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
# analyze.py [-h] [-input INPUT] [-trace TRACE] [-graph GRAPH] [--pcap PCAP]
# Details when running analyze.py -h
#
# To install on this machine: gnuplot, gnuplot.py, numpy, mptcptrace, tcptrace,
# xpl2gpl, tshark, tcpreplay

from __future__ import print_function

##################################################
##                   IMPORTS                    ##
##################################################

from common import *
from numpy import *

import argparse
import glob
import Gnuplot
import os
import os.path
import pickle
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback

from multiprocessing import Process


class cd:

    """Context manager for changing the current working directory"""

    def __init__(self, newPath):
        self.newPath = newPath

    def __enter__(self):
        self.savedPath = os.getcwd()
        os.chdir(self.newPath)

    def __exit__(self, etype, value, traceback):
        os.chdir(self.savedPath)

##################################################
##                  CONSTANTS                   ##
##################################################

# The default input directory (with .pcap and .pcap.gz files)
DEF_IN_DIR = 'input'
# The default traces directory (kind of temparary directory, where traces
# will be stored)
DEF_TRACE_DIR = 'traces'
# The default graph directory (output directory for graphes)
DEF_GRAPH_DIR = 'graphs'
# The default stat directory
DEF_STAT_DIR = 'stats'
# The default number of threads
DEF_NB_THREADS = 1
# IPv4 localhost address
LOCALHOST_IPv4 = '127.0.0.1'
# Port number of RedSocks
PORT_RSOCKS = '8123'
# Prefix of the Wi-Fi interface IP address
PREFIX_WIFI_IF = '192.168.'
# Size of Latin alphabet
SIZE_LAT_ALPH = 26
# mptcptrace file identifier in csv filename for sequence number informations
MPTCP_SEQ_FNAME = '_seq_'
# mptcptrace file identifier in csv filename for subflow number informations
MPTCP_SF_FNAME = '_sf_'

##################################################
##                   ARGUMENTS                  ##
##################################################

parser = argparse.ArgumentParser(
    description="Analyze pcap files of TCP or MPTCP connections")
parser.add_argument("-i",
    "--input", help="input directory of the (possibly compressed) pcap files", default=DEF_IN_DIR)
parser.add_argument("-t",
    "--trace", help="temporary directory that will be used to store uncompressed "
                    + "pcap files", default=DEF_TRACE_DIR)
parser.add_argument("-g",
    "--graph", help="directory where the graphs of the pcap files will be stored", default=DEF_GRAPH_DIR)
parser.add_argument("-s",
    "--stat", help="directory where the stats of the pcap files will be stored", default=DEF_STAT_DIR)
parser.add_argument("-p",
    "--pcap", help="analyze only pcap files containing the given string", default="")
parser.add_argument("-j",
    "--threads", type=int, help="process the analyse separated threads", default=DEF_NB_THREADS)
parser.add_argument("-l",
    "--stderr", help="log to stderr", action="store_true")
parser.add_argument("-k",
    "--keep", help="keep the original file with -k option of gunzip, if it exists",
                    action="store_true")
parser.add_argument("-c",
    "--clean", help="remove noisy traffic on lo", action="store_true")
parser.add_argument("-C",
    "--not-correct", help="do not correct traces, implies no preprocessing", action="store_true")
parser.add_argument("-G",
    "--not-graph", help="do not produce graphes and keep corrected traces, implies -P", action="store_true")
parser.add_argument("-P",
    "--not-purge", help="do not remove corrected traces", action="store_true")
args = parser.parse_args()

in_dir_exp = os.path.abspath(os.path.expanduser(args.input))
trace_dir_exp = os.path.abspath(os.path.expanduser(args.trace))
graph_dir_exp = os.path.abspath(os.path.expanduser(args.graph))
stat_dir_exp = os.path.abspath(os.path.expanduser(args.stat))

if args.stderr:
    print_out = sys.stderr
else:
    print_out = sys.stdout

##################################################
##                 PREPROCESSING                ##
##################################################

pcap_list = []
check_directory_exists(trace_dir_exp)
for dirpath, dirnames, filenames in os.walk(in_dir_exp):
    for fname in filenames:
        if args.pcap in fname:
            # Files from UI tests will be compressed; unzip them
            if fname.endswith('.gz'):
                output_file = os.path.join(trace_dir_exp, fname[:-3])
                if args.not_correct:
                    pcap_list.append(output_file)
                else:
                    print("Uncompressing " + fname + " to " + trace_dir_exp, file=print_out)
                    output = open(output_file, 'w')
                    cmd = ['gunzip', '-c', '-9', os.path.join(dirpath, fname)]
                    if args.keep:
                        cmd.insert(1, '-k')
                    if subprocess.call(cmd, stdout=output) != 0:
                        print("Error when uncompressing " + fname, file=sys.stderr)
                    else:
                        pcap_list.append(output_file)
                    output.close()
            elif fname.endswith('.pcap'):
                output_file = os.path.join(trace_dir_exp, fname)
                if args.not_correct:
                    pcap_list.append(output_file)
                else:
                    # Move the file to out_dir_exp
                    print("Copying " + fname + " to " + trace_dir_exp, file=print_out)
                    cmd = ['cp', os.path.join(dirpath, fname), output_file]
                    if subprocess.call(cmd, stdout=print_out) != 0:
                        print("Error when moving " + fname, file=sys.stderr)
                    else:
                        pcap_list.append(output_file)
            else:
                print(fname + ": not in a valid format, skipped", file=sys.stderr)
                continue


def clean_loopback_pcap(pcap_fname):
    """ Remove noisy traffic (port 1984), see netstat """
    tmp_pcap = "tmp.pcap"
    cmd = ['tshark', '-Y', '!(tcp.dstport==1984||tcp.srcport==1984)&&!((ip.src==127.0.0.1)&&(ip.dst==127.0.0.1))', '-r',
           pcap_fname, '-w', tmp_pcap, '-F', 'pcap']
    if subprocess.call(cmd, stdout=print_out) != 0:
        print("Error in cleaning " + pcap_fname, file=sys.stderr)
        return
    cmd = ['mv', tmp_pcap, pcap_fname]
    if subprocess.call(cmd, stdout=print_out) != 0:
        print("Error in moving " + tmp_pcap + " to " + pcap_fname, file=sys.stderr)


def save_connections(pcap_fname, connections):
    """ Using the name pcap_fname, save the statistics about connections """
    stat_fname = os.path.join(
        stat_dir_exp, os.path.basename(pcap_fname)[:-5])
    try:
        stat_file = open(stat_fname, 'w')
        pickle.dump(connections, stat_file)
        stat_file.close()
    except IOError as e:
        print(str(e) + ': no stat file for ' + pcap_fname, file=sys.stderr)


def get_connection_data_with_ip_port_tcp(connections, ip, port, dst=True):
    """ Get data for TCP connection with destination IP ip and port port in connections
        If no connection found, return None
        Support for dst=False will be provided if needed
    """
    for conn, data in connections.iteritems():
        if data[DADDR] == ip and data[DPORT] == port:
            return data

    # If reach this, no matching connection found
    return None


def copy_remain_pcap_file(pcap_fname):
    """ Given a pcap filename, return the filename of a copy, used for correction of traces """
    remain_pcap_fname = pcap_fname[:-5] + "__rem.pcap"
    cmd = ['cp', pcap_fname, remain_pcap_fname]
    if subprocess.call(cmd, stdout=print_out) != 0:
        print("Error when copying " + pcap_fname + ": skip tcp correction", file=sys.stderr)
        return None
    return remain_pcap_fname


def split_and_replace(pcap_fname, remain_pcap_fname, data, other_data, num):
    """ Split remain_pcap_fname and replace DADDR and DPORT of data by SADDR and DADDR of other_data
        num will be the numerotation of the splitted file
    """
    # Split on the port criterion
    condition = '(tcp.srcport==' + \
        data[SPORT] + ')or(tcp.dstport==' + data[SPORT] + ')'
    tmp_split_fname = pcap_fname[:-5] + "__tmp.pcap"
    cmd = ['tshark', '-r', remain_pcap_fname, '-Y', condition, '-w', tmp_split_fname]
    if subprocess.call(cmd, stdout=print_out) != 0:
        print(
            "Error when tshark port " + data[SPORT] + ": skip tcp correction", file=sys.stderr)
        return -1
    tmp_remain_fname = pcap_fname[:-5] + "__tmprem.pcap"
    cmd[4] = "!(" + condition + ")"
    cmd[6] = tmp_remain_fname
    if subprocess.call(cmd, stdout=print_out) != 0:
        print(
            "Error when tshark port !" + data[SPORT] + ": skip tcp correction", file=sys.stderr)
        return -1
    cmd = ['mv', tmp_remain_fname, remain_pcap_fname]
    if subprocess.call(cmd, stdout=print_out) != 0:
        print(
            "Error when moving " + tmp_remain_fname + " to " + remain_pcap_fname +  ": skip tcp correction", file=sys.stderr)
        return -1

    # Replace meaningless IP and port with the "real" values
    split_fname = pcap_fname[:-5] + "__" + str(num) + ".pcap"
    cmd = ['tcprewrite',
           "--portmap=" + data[DPORT] + ":" + other_data[SPORT],
           "--pnat=" + data[DADDR] + ":" + other_data[SADDR],
           "--infile=" + tmp_split_fname,
           "--outfile=" + split_fname]
    if subprocess.call(cmd, stdout=print_out) != 0:
        print(
            "Error with tcprewrite " + data[SPORT] + ": skip tcp correction", file=sys.stderr)
        return -1
    os.remove(tmp_split_fname)
    return 0


def merge_and_clean_sub_pcap(pcap_fname):
    """ Merge pcap files with name beginning with pcap_fname followed by two underscores and delete
        them
    """
    cmd = ['mergecap', '-w', pcap_fname]
    for subpcap_fname in glob.glob(pcap_fname[:-5] + '__*.pcap'):
        cmd.append(subpcap_fname)

    if subprocess.call(cmd, stdout=print_out) != 0:
        print(
            "Error with mergecap " + pcap_fname + ": skip tcp correction", file=sys.stderr)
        return
    for subpcap_fname in cmd[3:]:
        os.remove(subpcap_fname)

##################################################
##                  MPTCPTRACE                  ##
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


def extract_mptcp_flow_data(out_file):
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
            connections[current_connection] = {}

        # Case 2: line for a subflow
        elif current_connection is not False and line.startswith("\tSubflow"):
            # A typical line:
            #   Subflow 0 with wscale : 6 0 IPv4 sport 59570 dport 443 saddr
            # 37.185.171.74 daddr 194.78.99.114
            words = line.split()
            sub_flow_id = words[1]
            connections[current_connection][sub_flow_id] = {}
            index_wscale = words.index("wscale")
            connections[current_connection][sub_flow_id][
                WSCALESRC] = words[index_wscale + 2]
            connections[current_connection][sub_flow_id][
                WSCALEDST] = words[index_wscale + 3]
            connections[current_connection][sub_flow_id][
                TYPE] = words[index_wscale + 4]
            index = words.index("sport")
            while index + 1 < len(words):
                attr = words[index]
                value = words[index + 1]
                connections[current_connection][sub_flow_id][attr] = value
                index += 2

        # Case 3: skip the line (no more current connection)
        else:
            current_connection = False
    return connections


def indicates_wifi_or_rmnet(data):
    """ Given data of a mptcp connection subflow, indicates if comes from wifi or rmnet """
    if data[SADDR].startswith(PREFIX_WIFI_IF) or data[DADDR].startswith(PREFIX_WIFI_IF):
        data[IF] = WIFI
    else:
        data[IF] = RMNET


def interesting_mptcp_graph(csv_fname, connections):
    """ Return True if the MPTCP graph is worthy, else False
        This function assumes that a graph is interesting if it has at least one connection that
        if not 127.0.0.1 -> 127.0.0.1
        Note that is the graph is interesting and IPv4, indicates if the traffic is Wi-Fi or rmnet
    """
    connection_id = get_connection_id(csv_fname)
    interesting = False
    for sub_flow_id, data in connections[connection_id].iteritems():
        # There could have "pure" data in the connection
        if isinstance(data, dict):
            # Only had the case for IPv4, but what is its equivalent in IPv6?
            if not data[TYPE] == 'IPv4':
                interesting = True
            if not (data[SADDR] == LOCALHOST_IPv4 and data[DADDR] == LOCALHOST_IPv4):
                indicates_wifi_or_rmnet(data)
                interesting = True
    return interesting


def get_begin_values(first_line):
    split_line = first_line.split(',')
    return float(split_line[0]), int(split_line[1])


def write_graph_csv(csv_graph_tmp_dir, csv_fname, data, begin_time, begin_seq):
    """ Write in the graphs directory a new csv file containing relative values
        for plotting them
        Exit the program if an IOError is raised
    """
    try:
        graph_fname = os.path.join(csv_graph_tmp_dir, csv_fname)
        graph_file = open(graph_fname, 'w')
        # Modify lines for that
        for line in data:
            split_line = line.split(',')
            time = float(split_line[0]) - begin_time
            seq = int(split_line[1]) - begin_seq
            graph_file.write(str(time) + ',' + str(seq) + '\n')
        graph_file.close()
    except IOError as e:
        print('IOError for graph file with ' + csv_fname + ': stop', file=sys.stderr)
        exit(1)


def generate_title(csv_fname, connections):
    """ Generate the title for a mptcp connection """

    connection_id = get_connection_id(csv_fname)
    title = "flows:" + str(count_mptcp_subflows(connections[connection_id])) + " "

    # If not reverse, correct order, otherwise reverse src and dst
    reverse = is_reverse_connection(csv_fname)

    # Show all details of the subflows
    for sub_flow_id, data in connections[connection_id].iteritems():
        # There could have "pure" data in the connection
        if isinstance(data, dict):
            # \n must be interpreted as a raw type to works with GnuPlot.py
            title += r'\n' + "sf: " + sub_flow_id + " "
            if reverse:
                title += "(" + data[WSCALEDST] + " " + data[WSCALESRC] + ") "
                title += data[DADDR] + ":" + data[DPORT] + \
                    " -> " + data[SADDR] + ":" + data[SPORT]
            else:
                title += "(" + data[WSCALESRC] + " " + data[WSCALEDST] + ") "
                title += data[SADDR] + ":" + data[SPORT] + \
                    " -> " + data[DADDR] + ":" + data[DPORT]
            if IF in data:
                title += " [" + data[IF] + "]"
    return title


def create_graph_csv(pcap_fname, csv_fname, connections):
    """ Generate pdf for the csv file of the pcap file
    """
    # First see if useful to show the graph
    if not interesting_mptcp_graph(csv_fname, connections):
        return
    try:
        csv_file = open(csv_fname)
        data = csv_file.readlines()
    except IOError as e:
        print('IOError for ' + csv_fname + ': skipped', file=sys.stderr)
        return

    # If file was generated, the csv is not empty
    data_split = map(lambda x: x.split(','), data)
    data_plot = map(lambda x: map(lambda y: float(y), x), data_split)

    g = Gnuplot.Gnuplot(debug=0)
    g('set title "' + generate_title(csv_fname, connections) + '"')
    g('set style data linespoints')
    g.xlabel('Time [s]')
    g.ylabel('Sequence number')
    g.plot(data_plot)
    pdf_fname = os.path.join(graph_dir_exp,
                             os.path.basename(pcap_fname)[:-5] + "_" + csv_fname[:-4] + '.pdf')
    g.hardcopy(filename=pdf_fname, terminal='pdf')
    g.reset()


def process_mptcptrace_cmd(cmd, pcap_fname):
    """ Launch the command cmd given in argument, and return a dictionary containing information
        about connections of the pcap file analyzed
    """
    pcap_flow_data = pcap_fname[:-5] + '.out'
    flow_data_file = open(pcap_flow_data, 'w+')
    if subprocess.call(cmd, stdout=flow_data_file) != 0:
        print("Error of mptcptrace with " + pcap_fname + "; skip process", file=sys.stderr)
        return

    connections = extract_mptcp_flow_data(flow_data_file)
    # Don't forget to close and remove pcap_flow_data
    flow_data_file.close()
    os.remove(pcap_flow_data)
    return connections


# We can't change dir per thread, we should use processes
def process_mptcp_trace(pcap_fname):
    """ Process a mptcp pcap file and generate graphs of its subflows """
    csv_tmp_dir = tempfile.mkdtemp(dir=os.getcwd())
    with cd(csv_tmp_dir):
        cmd = ['mptcptrace', '-f', pcap_fname, '-s', '-w', '2']
        connections = process_mptcptrace_cmd(cmd, pcap_fname)

        csv_graph_tmp_dir = tempfile.mkdtemp(dir=graph_dir_exp)
        # The mptcptrace call will generate .csv files to cope with

        # First see all csv files, to detect the relative 0 of all connections
        relative_start = float("inf")
        for csv_fname in glob.glob('*.csv'):
            if MPTCP_SEQ_FNAME in csv_fname:
                try:
                    csv_file = open(csv_fname)
                    data = csv_file.readlines()
                    if not data == [] and len(data) > 1:
                        begin_time, begin_seq = get_begin_values(data[0])
                        if begin_time < relative_start and not begin_time == 0.0:
                            relative_start = begin_time
                    csv_file.close()
                except IOError as e:
                    print('IOError for ' + csv_fname + ': skipped', file=sys.stderr)
                    continue
                except ValueError as e:
                    print('ValueError for ' + csv_fname + ': skipped', file=sys.stderr)
                    continue

        # Then really process csv files
        for csv_fname in glob.glob('*.csv'):
            if MPTCP_SEQ_FNAME in csv_fname:
                try:
                    csv_file = open(csv_fname)
                    data = csv_file.readlines()
                    # Check if there is data in file (and not only one line of 0s)
                    if not data == [] and len(data) > 1:
                        # Collect begin time and seq num to plot graph starting at 0
                        begin_time, begin_seq = get_begin_values(data[0])

                        write_graph_csv(csv_graph_tmp_dir, csv_fname, data, relative_start, begin_seq)

                    csv_file.close()
                    # Remove the csv file
                    os.remove(csv_fname)

                except IOError as e:
                    print('IOError for ' + csv_fname + ': skipped', file=sys.stderr)
                    continue
                except ValueError as e:
                    print('ValueError for ' + csv_fname + ': skipped', file=sys.stderr)
                    continue

        with cd(csv_graph_tmp_dir):
            for csv_fname in glob.glob('*.csv'):
                # No point to plot information on subflows (as many points as there are subflows)
                if MPTCP_SF_FNAME not in csv_fname:
                    create_graph_csv(pcap_fname, csv_fname, connections)
                # Remove the csv file
                os.remove(csv_fname)

        # Save connections info
        save_connections(pcap_fname, connections)

        # Remove temp dirs
        shutil.rmtree(csv_graph_tmp_dir)

    shutil.rmtree(csv_tmp_dir)

##################################################
##                   TCPTRACE                   ##
##################################################


def convert_number_to_letter(nb_conn):
    """ Given an integer, return the (nb_conn)th letter of the alphabet (zero-based index) """
    return chr(ord('a') + nb_conn)


def get_prefix_name(nb_conn):
    """ Given an integer, return the (nb_conn)th prefix, based on the alphabet (zero-based index)"""
    if nb_conn >= SIZE_LAT_ALPH:
        mod_nb = nb_conn % SIZE_LAT_ALPH
        div_nb = nb_conn / SIZE_LAT_ALPH
        return get_prefix_name(div_nb - 1) + convert_number_to_letter(mod_nb)
    else:
        return convert_number_to_letter(nb_conn)


def convert_number_to_name(nb_conn):
    """ Given an integer, return a name of type 'a2b', 'aa2ab',... """
    if nb_conn >= (SIZE_LAT_ALPH / 2):
        mod_nb = nb_conn % (SIZE_LAT_ALPH / 2)
        div_nb = nb_conn / (SIZE_LAT_ALPH / 2)
        prefix = get_prefix_name(div_nb - 1)
        return prefix + convert_number_to_letter(2 * mod_nb) + '2' + prefix \
            + convert_number_to_letter(2 * mod_nb + 1)
    else:
        return convert_number_to_letter(2 * nb_conn) + '2' + convert_number_to_letter(2 * nb_conn + 1)


def detect_ipv4(data):
    """ Given the dictionary of a TCP connection, add the type IPv4 if it is an IPv4 connection """
    saddr = data[SADDR]
    daddr = data[DADDR]
    num_saddr = saddr.split('.')
    num_daddr = daddr.split('.')
    if len(num_saddr) == 4 and len(num_daddr) == 4:
        data[TYPE] = 'IPv4'


def compute_duration(info):
    """ Given the output of tcptrace as an array, compute the duration of a tcp connection
        The computation done (in term of tcptrace's attributes) is last_packet - first_packet
    """
    first_packet = float(info[5])
    last_packet = float(info[6])
    return last_packet - first_packet


def extract_tcp_flow_data(out_file):
    """ Given an (open) file, return a dictionary of as many elements as there are tcp flows """
    # Return at the beginning of the file
    out_file.seek(0)
    raw_data = out_file.readlines()
    connections = {}
    # The replacement of whitespaces by nothing prevents possible bugs if we use
    # additional information from tcptrace
    data = map(lambda x: x.replace(" ", ""), raw_data)
    for line in data:
        # Case 1: line start with #; skip it
        if not line.startswith("#"):
            info = line.split(',')
            # Case 2: line is empty or line is the "header line"; skip it
            if len(info) > 1 and is_number(info[0]):
                # Case 3: line begin with number --> extract info
                nb_conn = info[0]
                conn = convert_number_to_name(int(info[0]) - 1)
                connections[conn] = {}
                connections[conn][SADDR] = info[1]
                connections[conn][DADDR] = info[2]
                connections[conn][SPORT] = info[3]
                connections[conn][DPORT] = info[4]
                detect_ipv4(connections[conn])
                connections[conn][DURATION] = compute_duration(info)
                connections[conn][PACKS_S2D] = int(info[7])
                connections[conn][PACKS_D2S] = int(info[8])
                # Note that this count is about unique_data_bytes
                connections[conn][BYTES_S2D] = int(info[21])
                connections[conn][BYTES_D2S] = int(info[22])
                # TODO maybe extract more information

    return connections


def interesting_tcp_graph(flow_name, connections):
    """ Return True if the MPTCP graph is worthy, else False
        This function assumes that a graph is interesting if it has at least one connection that
        if not 127.0.0.1 -> 127.0.0.1
        Note that is the graph is interesting and IPv4, indicates if the traffic is Wi-Fi or rmnet
    """
    if not connections[flow_name][TYPE] == 'IPv4':
        return True
    if not (connections[flow_name][SADDR] == LOCALHOST_IPv4 and
            connections[flow_name][DADDR] == LOCALHOST_IPv4):
        indicates_wifi_or_rmnet(connections[flow_name])
        return True
    return False


def prepare_gpl_file(pcap_fname, gpl_fname):
    """ Return a gpl file name of a ready-to-use gpl file or None if an error
        occurs
    """
    try:
        gpl_fname_ok = gpl_fname[:-4] + '_ok.gpl'
        gpl_file = open(gpl_fname, 'r')
        gpl_file_ok = open(gpl_fname_ok, 'w')
        data = gpl_file.readlines()
        # Copy everything but the last 4 lines
        for line in data[:-4]:
            gpl_file_ok.write(line)
        # Give the pdf filename where the graph will be stored
        pdf_fname = os.path.join(graph_dir_exp,
                                 os.path.basename(pcap_fname)[:-5]
                                 + "_" + gpl_fname[:-4]
                                 + '.pdf')

        # Needed to give again the line with all data (5th line from the end)
        # Better to reset the plot (to avoid potential bugs)
        to_write = "set output '" + pdf_fname + "'\n" \
            + "set terminal pdf\n" \
            + data[-5] \
            + "set terminal pdf\n" \
            + "set output\n" \
            + "reset\n"
        gpl_file_ok.write(to_write)
        # Don't forget to close files
        gpl_file.close()
        gpl_file_ok.close()
        return gpl_fname_ok
    except IOError as e:
        print('IOError for graph file with ' + gpl_fname + ': skip', file=sys.stderr)
        return None


def get_flow_name(xpl_fname):
    """ Return the flow name in the form 'a2b' (and not 'b2a') """
    # Basic information is contained between the two last '_'
    last_us_index = xpl_fname.rindex("_")
    nearly_last_us_index = xpl_fname.rindex("_", 0, last_us_index)
    flow_name = xpl_fname[nearly_last_us_index + 1:last_us_index]

    # Need to check if we need to reverse the flow name
    two_index = flow_name.index("2")
    left_letter = flow_name[two_index - 1]
    right_letter = flow_name[-1]
    if right_letter < left_letter:
        # Swap those two characters
        chars = list(flow_name)
        chars[two_index - 1] = right_letter
        chars[-1] = left_letter
        return ''.join(chars)
    else:
        return flow_name


def process_tcptrace_cmd(cmd, pcap_fname):
    """ Launch the command cmd given in argument, and return a dictionary containing information
        about connections of the pcap file analyzed
        Options -n, -l and --csv should be set
    """
    pcap_flow_data = pcap_fname[:-5] + '.out'
    flow_data_file = open(pcap_flow_data, 'w+')
    if subprocess.call(cmd, stdout=flow_data_file) != 0:
        print("Error of tcptrace with " + pcap_fname + "; skip process", file=sys.stderr)
        return
    connections = extract_tcp_flow_data(flow_data_file)

    # Don't forget to close and remove pcap_flow_data
    flow_data_file.close()
    os.remove(pcap_flow_data)
    return connections


def correct_trace(pcap_fname):
    """ Make the link between two unidirectional connections that form one bidirectional one
        Do this also for mptcp, because mptcptrace will not be able to find all conversations
    """
    cmd = ['tcptrace', '-n', '-l', '--csv', pcap_fname]
    connections = process_tcptrace_cmd(cmd, pcap_fname)
    # Create the remaining_file
    remain_pcap_fname = copy_remain_pcap_file(pcap_fname)
    if not remain_pcap_fname:
        return

    num = 0
    for conn, data in connections.iteritems():
        if data[DADDR] == LOCALHOST_IPv4 and data[DPORT] == PORT_RSOCKS:
            other_data = get_connection_data_with_ip_port_tcp(
                connections, data[SADDR], data[SPORT])
            if other_data:
                if split_and_replace(pcap_fname, remain_pcap_fname, data, other_data, num) != 0:
                    print("Stop correcting trace " + pcap_fname, file=print_out)
                    return
        num += 1
        print(os.path.basename(pcap_fname) + ": Corrected: " + str(num) + "/" + str(len(connections)), file=print_out)

    # Merge small pcap files into a unique one
    merge_and_clean_sub_pcap(pcap_fname)


def process_tcp_trace(pcap_fname):
    """ Process a tcp pcap file and generate graphs of its connections """
    # -C for color, -S for sequence numbers, -T for throughput graph
    # -zxy to plot both axes to 0
    # -n to avoid name resolution
    # -y to remove some noise in sequence graphs
    # -l for long output
    # --csv for csv file
    cmd = ['tcptrace', '--output_dir=' + os.getcwd(),
        '--output_prefix=' + os.path.basename(pcap_fname[:-5]) + '_', '-C', '-S', '-T', '-zxy',
        '-n', '-y', '-l', '--csv', '--noshowzwndprobes', '--noshowoutorder', '--noshowrexmit',
        '--noshowsacks', '--noshowzerowindow', '--noshowurg', '--noshowdupack3',
        '--noshowzerolensegs', pcap_fname]

    connections = process_tcptrace_cmd(cmd, pcap_fname)

    # The tcptrace call will generate .xpl files to cope with
    for xpl_fname in glob.glob(os.path.join(os.getcwd(), os.path.basename(pcap_fname[:-5]) + '*.xpl')):
        flow_name = get_flow_name(xpl_fname)
        if interesting_tcp_graph(flow_name, connections):
            cmd = ['xpl2gpl', xpl_fname]
            if subprocess.call(cmd, stdout=print_out) != 0:
                print("Error of xpl2gpl with " + xpl_fname + "; skip xpl file", file=sys.stderr)
                continue
            prefix_fname = os.path.basename(xpl_fname)[:-4]
            gpl_fname = prefix_fname + '.gpl'
            gpl_fname_ok = prepare_gpl_file(pcap_fname, gpl_fname)
            if gpl_fname_ok:
                devnull = open(os.devnull, 'w')
                cmd = ['gnuplot', gpl_fname_ok]
                if subprocess.call(cmd, stdout=devnull) != 0:
                    print(
                        "Error of tcptrace with " + pcap_fname + "; skip process", file=sys.stderr)
                    return
                devnull.close()

            # Delete gpl, xpl and others files generated
            try:
                os.remove(gpl_fname)
                os.remove(gpl_fname_ok)
                try:
                    os.remove(prefix_fname + '.datasets')
                except OSError as e2:
                    # Throughput graphs have not .datasets file
                    pass
                os.remove(prefix_fname + '.labels')
            except OSError as e:
                print(str(e) + ": skipped", file=sys.stderr)
        try:
            os.remove(xpl_fname)
        except OSError as e:
            print(str(e) + ": skipped", file=sys.stderr)

    # Save connections info
    save_connections(pcap_fname, connections)

##################################################
##                   THREADS                    ##
##################################################

def launch_analyze_pcap(pcap_fname, clean, correct, graph, purge):
    pcap_filename = os.path.basename(pcap_fname)
    # Cleaning, if needed (in future pcap, tcpdump should do the job)
    if clean:
        clean_loopback_pcap(pcap_fname)
    # Prefix of the name determine the protocol used
    if pcap_filename.startswith('mptcp'):
        if correct:
            correct_trace(pcap_fname)
        # we need to change dir, do that in a new process
        if graph:
            p = Process(target=process_mptcp_trace, args=(pcap_fname,))
            p.start()
            p.join()
    elif pcap_filename.startswith('tcp'):
        if correct:
            correct_trace(pcap_fname)
        if graph:
            process_tcp_trace(pcap_fname)
    else:
        print(pcap_fname + ": don't know the protocol used; skipped", file=sys.stderr)

    print('End for file ' + pcap_fname, file=print_out)
    if purge and graph: # if we just want to correct traces, do not remove them
        os.remove(pcap_fname)

def thread_launch(thread_id, clean, correct, graph, purge):
    global pcap_list
    while True:
        try:
            pcap_fname = pcap_list.pop()
        except IndexError: # no more thread
            break
        print("Thread " + str(thread_id) + ": Analyze: " + pcap_fname, file=print_out)
        try:
            launch_analyze_pcap(pcap_fname, clean, correct, graph, purge)
        except:
            print(traceback.format_exc(), file=sys.stderr)
            print('Error when analyzing ' + pcap_fname + ': skip', file=sys.stderr)
    print("Thread " + str(thread_id) + ": End", file=print_out)

##################################################
##                     MAIN                     ##
##################################################

check_directory_exists(graph_dir_exp)
check_directory_exists(stat_dir_exp)
# If file is a .pcap, use it for (mp)tcptrace
pcap_list.reverse() # we will use pop: use the natural order

threads = []
args.threads = min(args.threads, len(pcap_list))
if args.threads > 1:
    # Launch new thread
    for thread_id in range(args.threads):
        thread = threading.Thread(target=thread_launch,
            args=(thread_id, args.clean,
                  not args.not_correct, not args.not_graph, not args.not_purge))
        thread.start()
        threads.append(thread)
    # Wait
    for thread in threads:
        thread.join()
else:
    thread_launch(0, args.clean, not args.not_correct, not args.not_graph, not args.not_purge)


print('End of analyze', file=print_out)
