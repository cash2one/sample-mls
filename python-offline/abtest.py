# ! /usr/bin/env python
#coding:utf8

"""

abtest 函数库， 包括内容：
    1. 流量切分
    2. 抽样变量赋值

"""

import yaml
import re
from hashlib import md5

HASH_DIVISOR = 100  # 取模的商数


class SampleVariable():
    """ """

    def __init__(self):
        self._conf = {}
        self._yaml_file = ''
        self._split = None

    def load_conf(self, file_name, split_obj):
        """ """
        self._yaml_file = file_name
        obj = yaml.load(open(file_name))
        self._conf = self.map_yaml_to_kv(obj)
        self._split = split_obj

        return self._conf

    def map_yaml_to_kv(self, obj):
        """
        将yaml的规范json转化为相对简单的kv json
        res['name1'] = {'default': 0, 'layer': 'A', 'condition':{'A1': 10, 'A2':100}}
        进行严格的正确性检查。segment命名不冲突，切分区间顺序不重叠。
        """
        res = {}
        for key, var in obj.iteritems():
            default = var.get('default', None)
            layer = var.get('layer', None)
            if default is None:
                raise ValueError('%s has no valid "default" value.' % key)
            if layer is None:
                raise ValueError('%s has no valid "layer" value.' % key)

            condition = {}
            for vals in var['condition']:
                seg_name = vals.get('segment', None)
                value = vals.get('value', None)
                if seg_name is None:
                    raise ValueError("condition in var %s has no 'segment' field." % key)
                if value is None:
                    raise ValueError("%s.%s has no 'value' field." % (key, seg_name))
                if not seg_name.startswith(layer):
                    raise ValueError("segment %s in layer %s in not allowed" % (seg_name, layer))
                if seg_name in condition:
                    raise ValueError("duplicated segment name in variable %s " % (key))
                condition[seg_name] = value

            res[key] = {'default': default, 'layer': layer, 'condition': condition}

        return res

    def get_sv(self, name, split_string):
        """ """
        variable = self._conf[name]
        if not split_string:
            return variable['default']
        conditions = variable['condition']
        
        segments = split_string.strip().split('_')
        for seg_name in segments:
            if seg_name in conditions:
                return conditions[seg_name]
        return variable['default']

    def get_sv_with_user_info(self, name, user_info=None):
        sv = self._conf[name]
        layer = sv['layer']
        split_string = self._split.split_with_user_info(user_info, layer)

        return self.get_sv(name, split_string)

    def get_sv_in_mob(self, name, log):
        """ mob实验抽样变量（sv）的取值"""
        sv = self._conf[name]
        layer = sv['layer']
        split_string = self._split.split_in_mob(layer, log)

        return self.get_sv(name, split_string)


class UserSplit():
    """ """

    def __init__(self):

        self._yaml_file = ''  # yaml配置文件路径
        self._conf = {}  # 配置内容
        self._split_string = ''  # 切分命中标示, 如 A1_B2


    def load_conf(self, file_name):
        """ """
        self._yaml_file = file_name
        obj = yaml.load(open(file_name))
        self._conf = self.map_yaml_to_kv(obj)

        return self._conf

    def map_yaml_to_kv(self, obj):
        """
        将yaml的规范json转化为相对简单的kv json
        res['A'] = {'hashcode': xxxx, 'segment':[('B1', 0, 9), ('B2', 10, 19)]}
        进行严格的正确性检查。segment命名不冲突，切分区间顺序不重叠。
        """
        res = {}
        for layer in obj:
            layer_name = layer['layer']
            if layer_name in res:
                raise ValueError('duplicated layer name %s.' % layer_name)

            res[layer_name] = {
                'hashcode': layer['hashcode'],
                'segment': []
            }

            last_seg = "NULL"
            last_start = -1
            last_end = 0
            for segment in layer['segment']:
                seg_name = segment['name']
                start = segment['start']
                end = segment['end']
                if seg_name in res[layer_name]['segment']:
                    raise ValueError("duplicated segment name %.%s" % (layer_name, seg_name))
                if start >= end or start < 0 or end > HASH_DIVISOR:
                    raise ValueError("segment %s.%s start end error [%s, %s)" % (layer_name, seg_name, start, end))
                if start < last_end:
                    raise ValueError("segment %s [%s,%s) and %s[%s,%s) overlaped in layer %s" % (
                        seg_name, start, end, last_seg, last_start, last_end, layer_name ))

                res[layer_name]['segment'].append((seg_name, start, end))

                last_seg = seg_name
                last_start = start
                last_end = end

        return res

    def get_split_string(self):
        return self._split_string

    def split(self, tag, layer_name):
        """
        获得tag字符串在layer下的签名。
        """
        if not tag:
            return ""

        layer = self._conf[layer_name]
        buf = layer['hashcode'] + tag.lower()
        residual = int(md5(buf).hexdigest()[-10:], 16) % HASH_DIVISOR

        self._split_string = ''
        segments = layer["segment"]
        for (seg_name, start, end) in segments:
            if residual < end:
                if residual >= start:
                    self._split_string = seg_name
                    return seg_name
                else:
                    return ''
        return ''

    def split_with_user_info(self, user_info, layer_name):
        """
        高级接口，用户提供关于user的定义，指定将user切分为layer上的segment。
        优先级：
           1. 有user_id，则按照user_id切分。
           2. 无user_id, 按照mlsid的逻辑顺序，先iso后android
               参考：https://app.yinxiang.com/shard/s9/sh/93a4223c-7bcd-4c40-938d-e67aa64ea653/1a8764d90e9a06bc191a935abdb1af65
        """
        if 'user_id' in user_info:
            return self.split(user_info['user_id'], layer_name)

        if 'access_token' in user_info:
            return self.split(user_info['access_token'], layer_name)

        if 'device_token' in user_info:
            return self.split(user_info['device_token'], layer_name)

        if 'open_udid' in user_info:
            return self.split(user_info['open_udid'], layer_name)

        if 'imei' in user_info:
            return self.split(user_info['imei'], layer_name)

        if 'macid' in user_info:
            return self.split(user_info['macid'], layer_name)

        return ''

    def split_in_web(self, layer_name, session_id):
        """
        web上的默认切分。 按照meilishuo_global_key
        :param layer_name:
        :return:
        """
        return self.split(session_id, layer_name)

    def split_in_mob(self, layer_name, log):
        """
        与php版本的输入不一致，php版本输入是MVC架构的controller，这里输入是一行log
        mobile_app_log_new
        """
        platform = ''
        access_token = ''
        device_token = ''
        open_udid = ''
        imei = ''
        macid = ''

        if isinstance(log, list):
            platform  = log[13]
            access_token = log[7]
            
            device_token = log[10]
            udid = log[17]

            imei = log[18]
            macid = log[27]
        else:
            platform = log.client_device
            access_token = log.access_token

            device_token = log.device_token
            open_udid = log.udid

            imei = log.imei
            macid = log.macid

        user_info = {}
        
        if platform == 'android':
            # iphone ipad android            
            if imei:
                user_info['imei'] = imei
            elif macid:
                user_info['macid'] = macid
            else:
                user_info['access_token'] = access_token
        elif platform in ('iphone', 'ipad'):
            if device_token and device_token != '00000000000000000000000000000000':
                user_info['device_token'] = device_token
            elif open_udid:
                user_info['open_udid'] = open_udid
            else:
                user_info['access_token'] = access_token
        else:
                user_info['access_token'] = access_token

        return self.split_with_user_info(user_info, layer_name)


if __name__ == "__main__":
    pass

