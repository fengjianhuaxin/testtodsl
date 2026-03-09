-- Auto-generated from Excel: ?????????_???????????.xlsx
SET NAMES utf8mb4;

DROP TABLE IF EXISTS `b_share_ppl_bas_hukou_reg`;
CREATE TABLE `b_share_ppl_bas_hukou_reg` (
  `cert_type` varchar(20) NOT NULL COMMENT '证件类型',
  `cert_no` varchar(50) NOT NULL COMMENT '证件号码',
  `name` varchar(50) DEFAULT NULL COMMENT '姓名',
  `gender` varchar(10) DEFAULT NULL COMMENT '性别',
  `nation` varchar(20) DEFAULT NULL COMMENT '民族',
  `birth_date` date DEFAULT NULL COMMENT '出生日期',
  `nationality` varchar(30) DEFAULT NULL COMMENT '国籍',
  `birth_place_country_region` varchar(100) DEFAULT NULL COMMENT '出生地-国家（地区）',
  `birth_place_prov_city_county` varchar(100) DEFAULT NULL COMMENT '出生地-省市县（区）?行政区划代码',
  `political_status` varchar(20) DEFAULT NULL COMMENT '政治面貌',
  `religion` varchar(30) DEFAULT NULL COMMENT '宗教信仰',
  `hukou_loc` varchar(100) DEFAULT NULL COMMENT '户籍所在地',
  `hukou_type` varchar(20) DEFAULT NULL COMMENT '户籍性质',
  `hukou_head_name` varchar(50) DEFAULT NULL COMMENT '户主姓名',
  `hukou_head_id_no` varchar(50) DEFAULT NULL COMMENT '户主身份号码',
  `hukou_book_no` varchar(50) DEFAULT NULL COMMENT '户口簿编号',
  `prev_name` varchar(50) DEFAULT NULL COMMENT '曾用名',
  `hukou_reg_org` varchar(100) DEFAULT NULL COMMENT '户籍登记机关',
  `hukou_prov_city_dist` varchar(100) DEFAULT NULL COMMENT '户籍地址-省市县（区）?行政区划代码',
  `hukou_street_code` varchar(300) DEFAULT NULL COMMENT '户籍地街路巷代码',
  `hukou_det_addr` varchar(200) DEFAULT NULL COMMENT '户籍地址-区划内详细地址',
  `military_service_status` varchar(50) DEFAULT NULL COMMENT '兵役状况',
  `actual_addr_prov_city_dist` varchar(100) DEFAULT NULL COMMENT '实际居住地址-省市县（区）?行政区划代码',
  `actual_detail_addr` varchar(200) DEFAULT NULL COMMENT '实际居住地址-区划内详细地址',
  `native_place` varchar(100) DEFAULT NULL COMMENT '籍贯_国家（地区）',
  `native_place_prov_city_dist` varchar(100) DEFAULT NULL COMMENT '籍贯_省市县（区）?行政区划代码',
  `native_place_detail_addr` varchar(200) DEFAULT NULL COMMENT '籍贯_区划内详细地址',
  `cancel_flag` varchar(50) DEFAULT NULL COMMENT '户籍注销标识',
  `cancel_date` date DEFAULT NULL COMMENT '户籍注销日期',
  `ep_default_data_digest` varchar(32) DEFAULT NULL COMMENT '数据摘要(MD5)',
  `ep_model_process_time` timestamp DEFAULT NULL COMMENT '模型结果加工时间',
  `ep_data_sync_time` timestamp DEFAULT NULL COMMENT '数据同步时间',
  `ep_src_table` varchar(100) DEFAULT NULL COMMENT '来源表',
  `ep_src_dept` varchar(100) DEFAULT NULL COMMENT '来源部门',
  PRIMARY KEY (`cert_type`, `cert_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='户籍登记信息';

DROP TABLE IF EXISTS `b_share_ppl_bas_birth_info`;
CREATE TABLE `b_share_ppl_bas_birth_info` (
  `cert_no` varchar(50) NOT NULL COMMENT '证件号码',
  `name` varchar(50) DEFAULT NULL COMMENT '姓名',
  `birth_cert_no` varchar(50) DEFAULT NULL COMMENT '出生医学证明编号',
  `birth_date` date DEFAULT NULL COMMENT '出生日期',
  `gender` varchar(10) DEFAULT NULL COMMENT '性别',
  `mother_name` varchar(50) DEFAULT NULL COMMENT '母亲姓名',
  `mother_cert_type` varchar(20) DEFAULT NULL COMMENT '母亲证件类型',
  `mother_cert_no` varchar(50) DEFAULT NULL COMMENT '母亲证件号码',
  `nationality` varchar(30) DEFAULT NULL COMMENT '国籍',
  `birth_place_country_region` varchar(100) DEFAULT NULL COMMENT '出生地-国家（地区）',
  `birth_place_prov_city_county` varchar(100) DEFAULT NULL COMMENT '出生地-省市县（区）?行政区划代码',
  `ethnic_group` varchar(20) DEFAULT NULL COMMENT '民族',
  `father_name` varchar(50) DEFAULT NULL COMMENT '父亲姓名',
  `father_cert_type` varchar(20) DEFAULT NULL COMMENT '父亲证件类型',
  `father_cert_no` varchar(50) DEFAULT NULL COMMENT '父亲证件号码',
  `ep_default_data_digest` varchar(32) DEFAULT NULL COMMENT '数据摘要(MD5)',
  `ep_model_process_time` timestamp DEFAULT NULL COMMENT '模型结果加工时间',
  `ep_data_sync_time` timestamp DEFAULT NULL COMMENT '数据同步时间',
  `ep_src_table` varchar(100) DEFAULT NULL COMMENT '来源表',
  `ep_src_dept` varchar(100) DEFAULT NULL COMMENT '来源部门',
  PRIMARY KEY (`cert_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='出生信息';

DROP TABLE IF EXISTS `b_share_ppl_bas_funeral_info`;
CREATE TABLE `b_share_ppl_bas_funeral_info` (
  `cert_type` varchar(20) NOT NULL COMMENT '证件类型',
  `cert_no` varchar(50) NOT NULL COMMENT '证件号码',
  `name` varchar(50) DEFAULT NULL COMMENT '姓名',
  `funeral_type` varchar(30) DEFAULT NULL COMMENT '殡葬类型',
  `funeral_date` date DEFAULT NULL COMMENT '殡葬日期',
  `ep_default_data_digest` varchar(32) DEFAULT NULL COMMENT '数据摘要(MD5)',
  `ep_model_process_time` timestamp DEFAULT NULL COMMENT '模型结果加工时间',
  `ep_data_sync_time` timestamp DEFAULT NULL COMMENT '数据同步时间',
  `ep_src_table` varchar(100) DEFAULT NULL COMMENT '来源表',
  `ep_src_dept` varchar(100) DEFAULT NULL COMMENT '来源部门',
  PRIMARY KEY (`cert_type`, `cert_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='殡葬信息';

DROP TABLE IF EXISTS `b_share_ppl_rel_marriage_rel`;
CREATE TABLE `b_share_ppl_rel_marriage_rel` (
  `cert_type` varchar(20) NOT NULL COMMENT '证件类型',
  `cert_no` varchar(50) NOT NULL COMMENT '证件号码',
  `name` varchar(100) DEFAULT NULL COMMENT '姓名',
  `spouse_cert_type` varchar(20) DEFAULT NULL COMMENT '配偶证件类型',
  `spouse_cert_no` varchar(50) DEFAULT NULL COMMENT '配偶证件号码',
  `spouse_name` varchar(100) DEFAULT NULL COMMENT '配偶姓名',
  `marital_status` varchar(20) DEFAULT NULL COMMENT '婚姻状况',
  `divorce_marriage_date` date DEFAULT NULL COMMENT '结离婚日期',
  `personal_nationality` varchar(30) DEFAULT NULL COMMENT '本人国籍',
  `spouse_nationality` varchar(30) DEFAULT NULL COMMENT '配偶国籍',
  `ep_default_data_digest` varchar(32) DEFAULT NULL COMMENT '数据摘要(MD5)',
  `ep_model_process_time` timestamp DEFAULT NULL COMMENT '模型结果加工时间',
  `ep_data_sync_time` timestamp DEFAULT NULL COMMENT '数据同步时间',
  `ep_src_table` varchar(100) DEFAULT NULL COMMENT '来源表',
  `ep_src_dept` varchar(100) DEFAULT NULL COMMENT '来源部门',
  PRIMARY KEY (`cert_type`, `cert_no`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='婚姻关系';

DROP TABLE IF EXISTS `b_share_ppl_rel_marriage_rel_his`;
CREATE TABLE `b_share_ppl_rel_marriage_rel_his` (
  `uuid` varchar(50) NOT NULL COMMENT '主键',
  `cert_type` varchar(20) DEFAULT NULL COMMENT '证件类型',
  `cert_no` varchar(50) DEFAULT NULL COMMENT '证件号码',
  `name` varchar(100) DEFAULT NULL COMMENT '姓名',
  `gender` varchar(10) DEFAULT NULL COMMENT '性别',
  `marital_item` varchar(20) DEFAULT NULL COMMENT '婚姻事项',
  `marital_status` varchar(20) DEFAULT NULL COMMENT '婚姻状况',
  `marital_status_change_cause` varchar(50) DEFAULT NULL COMMENT '婚姻状况变更原因',
  `marital_status_change_date` date DEFAULT NULL COMMENT '婚姻状况变更日期',
  `spouse_cert_type` varchar(20) DEFAULT NULL COMMENT '配偶证件类型',
  `spouse_cert_no` varchar(50) DEFAULT NULL COMMENT '配偶证件号码',
  `spouse_name` varchar(100) DEFAULT NULL COMMENT '配偶姓名',
  `reg_auth` varchar(100) DEFAULT NULL COMMENT '登记机关',
  `mar_type` varchar(50) DEFAULT NULL COMMENT '婚姻登记类型',
  `cert_num` varchar(100) DEFAULT NULL COMMENT '结离婚证字号',
  `div_jud_date` date DEFAULT NULL COMMENT '离婚判决时间',
  `div_eff_date` date DEFAULT NULL COMMENT '离婚判决生效日期',
  `personal_nationality` varchar(30) DEFAULT NULL COMMENT '本人国籍',
  `spouse_nationality` varchar(30) DEFAULT NULL COMMENT '配偶国籍',
  `ep_default_data_digest` varchar(32) DEFAULT NULL COMMENT '数据摘要(MD5)',
  `ep_model_process_time` timestamp DEFAULT NULL COMMENT '模型结果加工时间',
  `ep_data_sync_time` timestamp DEFAULT NULL COMMENT '数据同步时间',
  `ep_src_table` varchar(100) DEFAULT NULL COMMENT '来源表',
  `ep_src_dept` varchar(100) DEFAULT NULL COMMENT '来源部门',
  PRIMARY KEY (`uuid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='婚姻关系历史记录';
