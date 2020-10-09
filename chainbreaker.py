#!/usr/bin/python

# Author : n0fate
# E-Mail rapfer@gmail.com, n0fate@n0fate.com
#
# 10/7/2020 - Significant changes made by luke@socially-inept.net
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or (at
# your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
#
import struct
from pbkdf2 import pbkdf2
from schema import *
from schema import _APPL_DB_HEADER, _APPL_DB_SCHEMA, _TABLE_HEADER, _DB_BLOB, _GENERIC_PW_HEADER, \
    _KEY_BLOB_REC_HEADER, _KEY_BLOB, _SSGP, _INTERNET_PW_HEADER, _APPLE_SHARE_HEADER, _X509_CERT_HEADER, _SECKEY_HEADER, \
    _UNLOCK_BLOB, _KEYCHAIN_TIME, _INT, _FOUR_CHAR_CODE, _LV, _TABLE_ID, _RECORD_OFFSET
from pyDes import triple_des, CBC
from binascii import unhexlify, hexlify
import logging
import base64
import string


class Chainbreaker(object):
    ATOM_SIZE = 4
    KEYCHAIN_SIGNATURE = "kych"
    BLOCKSIZE = 8
    KEYLEN = 24
    MAGIC_CMS_IV = unhexlify('4adda22c79e82105')
    KEYCHAIN_LOCKED_SIGNATURE = '[Invalid Password / Keychain Locked]'
    KEYCHAIN_PASSWORD_HASH_FORMAT = "$keychain$*%s*%s*%s"

    def __init__(self, filepath, unlock_password=None, unlock_key=None, unlock_file=None):
        self._filepath = None
        self._unlock_password = None
        self._unlock_key = None
        self._unlock_file = None
        self._db_key = None

        self.kc_buffer = ''

        self.header = None
        self.schema_info = None
        self.table_list = None
        self.table_metadata = None
        self.record_list = None
        self.table_count = None
        self.table_enum = None
        self.symmetric_key_list = None
        self.symmetric_key_offset = None
        self.dbblob = None
        self.unlocked = False

        self.logger = logging.getLogger('Chainbreaker')

        self.key_list = {}

        self.db_key = None

        self.filepath = filepath

        if not self._is_valid_keychain():
            self.logger.warning('Keychain signature does not match. are you sure this is a valid keychain file?')

        self.unlock_password = unlock_password
        self.unlock_key = unlock_key
        self.unlock_file = unlock_file

    def dump_generic_passwords(self):
        entries = []
        try:
            table_metadata, generic_pw_list = self._get_table(
                self._get_table_offset(CSSM_DL_DB_RECORD_GENERIC_PASSWORD))

            for generic_pw_id in generic_pw_list:
                entries.append(
                    self._get_generic_password_record(self._get_table_offset(CSSM_DL_DB_RECORD_GENERIC_PASSWORD),
                                                      generic_pw_id))

        except KeyError:
            self.logger.warning('[!] Generic Password Table is not available')

        return entries

    def dump_internet_passwords(self):
        entries = []
        try:
            table_metadata, internet_pw_list = self._get_table(
                self._get_table_offset(CSSM_DL_DB_RECORD_INTERNET_PASSWORD))

            for internet_pw_id in internet_pw_list:
                entries.append(self._get_internet_password_record(
                    self._get_table_offset(CSSM_DL_DB_RECORD_INTERNET_PASSWORD), internet_pw_id))

        except KeyError:
            self.logger.warning('[!] Internet Password Table is not available')
        return entries

    def dump_appleshare_passwords(self):
        entries = []
        try:
            table_metadata, appleshare_pw_list = self._get_table(
                self._get_table_offset(CSSM_DL_DB_RECORD_APPLESHARE_PASSWORD))

            for appleshare_pw_offset in appleshare_pw_list:
                entries.append(self._get_appleshare_record(
                    self._get_table_offset(CSSM_DL_DB_RECORD_APPLESHARE_PASSWORD), appleshare_pw_offset))

        except KeyError:
            self.logger.warning('[!] Appleshare Records Table is not available')
        return entries

    def dump_x509_certificates(self):
        entries = []
        try:
            table_metadata, x509_cert_list = self._get_table(self._get_table_offset(CSSM_DL_DB_RECORD_X509_CERTIFICATE))

            for i, x509_cert_offset in enumerate(x509_cert_list, 1):
                entries.append(
                    self._get_x_509_record(self._get_table_offset(CSSM_DL_DB_RECORD_X509_CERTIFICATE),
                                           x509_cert_offset))

        except KeyError:
            self.logger.warning('[!] Certificate Table is not available')

        return entries

    def dump_public_keys(self):
        entries = []
        try:
            table_metadata, public_key_list = self._get_table(self._get_table_offset(CSSM_DL_DB_RECORD_PUBLIC_KEY))
            for public_key_offset in public_key_list:
                entries.append(
                    self._get_public_key_record(self._get_table_offset(CSSM_DL_DB_RECORD_PUBLIC_KEY),
                                                public_key_offset))
        except KeyError:
            self.logger.warning('[!] Public Key Table is not available')
        return entries

    def dump_private_keys(self):
        entries = []
        try:
            table_meta, private_key_list = self._get_table(self._get_table_offset(CSSM_DL_DB_RECORD_PRIVATE_KEY))
            for i, private_key_offset in enumerate(private_key_list, 1):
                entries.append(
                    self._get_private_key_record(self._get_table_offset(CSSM_DL_DB_RECORD_PRIVATE_KEY),
                                                 private_key_offset))

        except KeyError:
            self.logger.warning('[!] Private Key Table is not available')
        return entries

    def _read_keychain_to_buffer(self):
        try:
            with open(self.filepath, 'rb') as fp:
                self.kc_buffer = fp.read()

            if self.kc_buffer:
                self.header = _APPL_DB_HEADER(self.kc_buffer[:_APPL_DB_HEADER.STRUCT.size])
                self.schema_info, self.table_list = self._get_schema_info(self.header.SchemaOffset)
                self.table_metadata, self.record_list = self._get_table(self.table_list[0])
                self.table_count, self.table_enum = self._get_table_name_to_list(self.record_list, self.table_list)

                self.symmetric_key_offset = self.table_list[self.table_enum[CSSM_DL_DB_RECORD_METADATA]]

                self.base_addr = _APPL_DB_HEADER.STRUCT.size + self.symmetric_key_offset + 0x38
                self.dbblob = _DB_BLOB(self.kc_buffer[self.base_addr:self.base_addr + _DB_BLOB.STRUCT.size])


        except Exception as e:
            self.logger.critical("Unable to read keychain: %s" % (e))

    def _is_valid_keychain(self):
        if self.kc_buffer[0:4] != Chainbreaker.KEYCHAIN_SIGNATURE:
            return False
        return True

    def _generate_key_list(self):
        table_meta_data, symmetric_key_list = self._get_table(self._get_table_offset(CSSM_DL_DB_RECORD_SYMMETRIC_KEY))

        for symmetric_key_record in symmetric_key_list:
            keyblob, ciphertext, iv, return_value = self._get_keyblob_record(
                self._get_table_offset(CSSM_DL_DB_RECORD_SYMMETRIC_KEY), symmetric_key_record)
            if return_value == 0:
                password = self._keyblob_decryption(ciphertext, iv, self.db_key)
                if password != '':
                    self.key_list[keyblob] = password

    def _get_schema_info(self, offset):
        table_list = []
        _schema_info = _APPL_DB_SCHEMA(self.kc_buffer[offset:offset + _APPL_DB_SCHEMA.STRUCT.size])

        for i in xrange(_schema_info.TableCount):
            BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + _APPL_DB_SCHEMA.STRUCT.size
            table_list.append(_TABLE_ID(self.kc_buffer[BASE_ADDR + (Chainbreaker.ATOM_SIZE * i):BASE_ADDR + (
                    Chainbreaker.ATOM_SIZE * i) + Chainbreaker.ATOM_SIZE]).Value)

        return _schema_info, table_list

    def _get_table_offset(self, table_name):
        return self.table_list[self.table_enum[table_name]]

    def _get_table(self, offset):
        record_list = []

        BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + offset
        table_metadata = _TABLE_HEADER(self.kc_buffer[BASE_ADDR:BASE_ADDR + _TABLE_HEADER.STRUCT.size])
        RECORD_OFFSET_BASE = BASE_ADDR + _TABLE_HEADER.STRUCT.size

        record_count = 0
        offset = 0
        while table_metadata.RecordCount != record_count:
            record_offset = _RECORD_OFFSET(self.kc_buffer[
                                           RECORD_OFFSET_BASE + (Chainbreaker.ATOM_SIZE * offset):RECORD_OFFSET_BASE + (
                                                   Chainbreaker.ATOM_SIZE * offset) + Chainbreaker.ATOM_SIZE]).Value

            if (record_offset != 0x00) and (record_offset % 4 == 0):
                record_list.append(record_offset)
                record_count += 1
            offset += 1

        return table_metadata, record_list

    #
    def _get_table_name_to_list(self, record_list, table_list):
        table_dict = {}
        for count in xrange(len(record_list)):
            table_metadata, generic_list = self._get_table(table_list[count])
            table_dict[table_metadata.TableId] = count  # extract valid table list

        return len(record_list), table_dict

    def _get_keyblob_record(self, base_addr, offset):

        BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + base_addr + offset

        KeyBlobRecHeader = _KEY_BLOB_REC_HEADER(self.kc_buffer[BASE_ADDR:BASE_ADDR + _KEY_BLOB_REC_HEADER.STRUCT.size])

        record = self.kc_buffer[
                 BASE_ADDR + _KEY_BLOB_REC_HEADER.STRUCT.size:BASE_ADDR + KeyBlobRecHeader.RecordSize]  # password data area

        KeyBlobRecord = _KEY_BLOB(record[:+_KEY_BLOB.STRUCT.size])

        if SECURE_STORAGE_GROUP != str(record[KeyBlobRecord.TotalLength + 8:KeyBlobRecord.TotalLength + 8 + 4]):
            return '', '', '', 1

        CipherLen = KeyBlobRecord.TotalLength - KeyBlobRecord.StartCryptoBlob
        if CipherLen % Chainbreaker.BLOCKSIZE != 0:
            self.logger.debug("Bad ciphertext length.")
            return '', '', '', 1

        ciphertext = record[KeyBlobRecord.StartCryptoBlob:KeyBlobRecord.TotalLength]

        # match data, keyblob_ciphertext, Initial Vector, success
        return record[KeyBlobRecord.TotalLength + 8:KeyBlobRecord.TotalLength + 8 + 20], ciphertext, KeyBlobRecord.IV, 0

    def _get_encrypted_data_in_blob(self, BlobBuf):
        KeyBlob = _KEY_BLOB(BlobBuf[:_KEY_BLOB.STRUCT.size])

        if KeyBlob.CommonBlob.Magic != _KEY_BLOB.COMMON_BLOB_MAGIC:
            return '', ''

        KeyData = BlobBuf[KeyBlob.StartCryptoBlob:KeyBlob.TotalLength]
        return KeyBlob.IV, KeyData  # IV, Encrypted Data

    def _get_keychain_time(self, BASE_ADDR, pCol):
        if pCol <= 0:
            return ''
        else:
            return _KEYCHAIN_TIME(self.kc_buffer[BASE_ADDR + pCol:BASE_ADDR + pCol + _KEYCHAIN_TIME.STRUCT.size]).Time

    def _get_int(self, BASE_ADDR, pCol):
        if pCol <= 0:
            return 0
        else:
            return _INT(self.kc_buffer[BASE_ADDR + pCol:BASE_ADDR + pCol + 4]).Value

    def _get_four_char_code(self, BASE_ADDR, pCol):
        if pCol <= 0:
            return ''
        else:
            return _FOUR_CHAR_CODE(self.kc_buffer[BASE_ADDR + pCol:BASE_ADDR + pCol + 4]).Value

    def _get_lv(self, BASE_ADDR, pCol):
        if pCol <= 0:
            return ''

        str_length = _INT(self.kc_buffer[BASE_ADDR + pCol:BASE_ADDR + pCol + 4]).Value
        # 4byte arrangement
        if (str_length % 4) == 0:
            real_str_len = (str_length / 4) * 4
        else:
            real_str_len = ((str_length / 4) + 1) * 4

        try:
            data = _LV(self.kc_buffer[BASE_ADDR + pCol + 4:BASE_ADDR + pCol + 4 + real_str_len], real_str_len).Value
        except struct.error:
            self.logger.debug('LV string length is too long.')
            return ''

        return data

    #
    # ## decrypted dbblob area
    # ## Documents : http://www.opensource.apple.com/source/securityd/securityd-55137.1/doc/BLOBFORMAT
    # ## http://www.opensource.apple.com/source/libsecurity_keychain/libsecurity_keychain-36620/lib/StorageManager.cpp
    def _ssgp_decryption(self, ssgp, dbkey):
        return Chainbreaker._kcdecrypt(dbkey, _SSGP(ssgp).IV, ssgp[_SSGP.STRUCT.size:])

    # Documents : http://www.opensource.apple.com/source/securityd/securityd-55137.1/doc/BLOBFORMAT
    # source : http://www.opensource.apple.com/source/libsecurity_cdsa_client/libsecurity_cdsa_client-36213/lib/securestorage.cpp
    # magicCmsIV : http://www.opensource.apple.com/source/Security/Security-28/AppleCSP/AppleCSP/wrapKeyCms.cpp
    def _keyblob_decryption(self, encryptedblob, iv, dbkey):

        # magicCmsIV = unhexlify('4adda22c79e82105')
        plain = Chainbreaker._kcdecrypt(dbkey, Chainbreaker.MAGIC_CMS_IV, encryptedblob)

        if plain.__len__() == 0:
            return ''

        # now we handle the unwrapping. we need to take the first 32 bytes,
        # and reverse them.
        revplain = ''
        for i in range(32):
            revplain += plain[31 - i]

        # now the real key gets found. */
        plain = Chainbreaker._kcdecrypt(dbkey, iv, revplain)

        keyblob = plain[4:]

        if len(keyblob) != Chainbreaker.KEYLEN:
            self.logger.debug("Decrypted key length is not valid")
            return ''

        return keyblob

    #
    # # http://opensource.apple.com/source/libsecurity_keychain/libsecurity_keychain-55044/lib/KeyItem.cpp
    def _private_key_decryption(self, encryptedblob, iv):
        plain = Chainbreaker._kcdecrypt(self.db_key, Chainbreaker.MAGIC_CMS_IV, encryptedblob)

        if plain.__len__() == 0:
            return '', ''

        # now we handle the unwrapping. we need to take the first 32 bytes,
        # and reverse them.
        revplain = ''
        for i in range(len(plain)):
            revplain += plain[len(plain) - 1 - i]

        # now the real key gets found. */
        plain = Chainbreaker._kcdecrypt(self.db_key, iv, revplain)

        Keyname = plain[:12]  # Copied Buffer when user click on right and copy a key on Keychain Access
        keyblob = plain[12:]

        return Keyname, keyblob

    # ## Documents : http://www.opensource.apple.com/source/securityd/securityd-55137.1/doc/BLOBFORMAT
    def _generate_master_key(self, pw):
        return pbkdf2(pw, str(bytearray(self.dbblob.Salt)), 1000, Chainbreaker.KEYLEN)

    # ## find DBBlob and extract Wrapping key
    def _find_wrapping_key(self, master):
        # get cipher text area
        ciphertext = self.kc_buffer[
                     self.base_addr + self.dbblob.StartCryptoBlob:self.base_addr + self.dbblob.TotalLength]

        # decrypt the key
        plain = Chainbreaker._kcdecrypt(master, self.dbblob.IV, ciphertext)

        if plain.__len__() < Chainbreaker.KEYLEN:
            return ''

        dbkey = plain[:Chainbreaker.KEYLEN]

        # return encrypted wrapping key
        return dbkey

    def dump_keychain_password_hash(self):
        cyphertext = hexlify(
            self.kc_buffer[self.base_addr + self.dbblob.StartCryptoBlob:self.base_addr + self.dbblob.TotalLength])

        iv = hexlify(self.dbblob.IV)
        salt = hexlify(self.dbblob.Salt)

        return Chainbreaker.KEYCHAIN_PASSWORD_HASH_FORMAT % (salt, iv, cyphertext)

    def _get_appleshare_record(self, base_addr, offset):
        BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + base_addr + offset

        RecordMeta = _APPLE_SHARE_HEADER(self.kc_buffer[BASE_ADDR:BASE_ADDR + _APPLE_SHARE_HEADER.STRUCT.size])

        Buffer = self.kc_buffer[BASE_ADDR + _APPLE_SHARE_HEADER.STRUCT.size:BASE_ADDR + RecordMeta.RecordSize]

        ssgp, dbkey = self._extract_ssgp_and_dbkey(RecordMeta, Buffer)

        return self.AppleshareRecord(
            created=self._get_keychain_time(BASE_ADDR, RecordMeta.CreationDate & 0xFFFFFFFE),
            last_modified=self._get_keychain_time(BASE_ADDR, RecordMeta.ModDate & 0xFFFFFFFE),
            description=self._get_lv(BASE_ADDR, RecordMeta.Description & 0xFFFFFFFE),
            comment=self._get_lv(BASE_ADDR, RecordMeta.Comment & 0xFFFFFFFE),
            creator=self._get_four_char_code(BASE_ADDR, RecordMeta.Creator & 0xFFFFFFFE),
            type=self._get_four_char_code(BASE_ADDR, RecordMeta.Type & 0xFFFFFFFE),
            print_name=self._get_lv(BASE_ADDR, RecordMeta.PrintName & 0xFFFFFFFE),
            alias=self._get_lv(BASE_ADDR, RecordMeta.Alias & 0xFFFFFFFE),
            protected=self._get_lv(BASE_ADDR, RecordMeta.Protected & 0xFFFFFFFE),
            account=self._get_lv(BASE_ADDR, RecordMeta.Account & 0xFFFFFFFE),
            volume=self._get_lv(BASE_ADDR, RecordMeta.Volume & 0xFFFFFFFE),
            server=self._get_lv(BASE_ADDR, RecordMeta.Server & 0xFFFFFFFE),
            protocol_type=self._get_four_char_code(BASE_ADDR, RecordMeta.Protocol & 0xFFFFFFFE),
            address=self._get_lv(BASE_ADDR, RecordMeta.Address & 0xFFFFFFFE),
            signature=self._get_lv(BASE_ADDR, RecordMeta.Signature & 0xFFFFFFFE),
            ssgp=ssgp,
            dbkey=dbkey
        )

    def _get_private_key_record(self, base_addr, offset):
        record = self._get_key_record(base_addr, offset)

        if not self.db_key:
            keyname = privatekey = Chainbreaker.KEYCHAIN_LOCKED_SIGNATURE
        else:
            keyname, privatekey = self._private_key_decryption(record[10], record[9])
        return self.PrivateKeyRecord(
            print_name=record[0],
            label=record[1],
            key_class=KEY_TYPE[record[2]],
            private=record[3],
            key_type=record[4],
            key_size=record[5],
            effective_key_size=record[6],
            extracted=record[7],
            cssm_type=record[8],
            iv=record[9],
            key=record[10],
            key_name=keyname,
            private_key=privatekey,
        )

    def _get_public_key_record(self, base_addr, offset):
        record = self._get_key_record(base_addr, offset)
        return self.PublicKeyRecord(
            print_name=record[0],
            label=record[1],
            key_class=KEY_TYPE[record[2]],
            private=record[3],
            key_type=record[4],
            key_size=record[5],
            effective_key_size=record[6],
            extracted=record[7],
            cssm_type=record[8],
            iv=record[9],
            public_key=record[10],
        )

    def _get_key_record(self, base_addr, offset):  ## PUBLIC and PRIVATE KEY

        BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + base_addr + offset

        RecordMeta = _SECKEY_HEADER(self.kc_buffer[BASE_ADDR:BASE_ADDR + _SECKEY_HEADER.STRUCT.size])

        KeyBlob = self.kc_buffer[
                  BASE_ADDR + _SECKEY_HEADER.STRUCT.size:BASE_ADDR + _SECKEY_HEADER.STRUCT.size + RecordMeta.BlobSize]

        IV, Key = self._get_encrypted_data_in_blob(KeyBlob)

        return [self._get_lv(BASE_ADDR, RecordMeta.PrintName & 0xFFFFFFFE),
                self._get_lv(BASE_ADDR, RecordMeta.Label & 0xFFFFFFFE),
                self._get_int(BASE_ADDR, RecordMeta.KeyClass & 0xFFFFFFFE),
                self._get_int(BASE_ADDR, RecordMeta.Private & 0xFFFFFFFE),
                CSSM_ALGORITHMS[self._get_int(BASE_ADDR, RecordMeta.KeyType & 0xFFFFFFFE)],
                self._get_int(BASE_ADDR, RecordMeta.KeySizeInBits & 0xFFFFFFFE),
                self._get_int(BASE_ADDR, RecordMeta.EffectiveKeySize & 0xFFFFFFFE),
                self._get_int(BASE_ADDR, RecordMeta.Extractable & 0xFFFFFFFE),
                STD_APPLE_ADDIN_MODULE[
                    str(self._get_lv(BASE_ADDR, RecordMeta.KeyCreator & 0xFFFFFFFE)).split('\x00')[0]],
                IV,
                Key]

    def _get_x_509_record(self, base_addr, offset):
        BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + base_addr + offset
        RecordMeta = _X509_CERT_HEADER(self.kc_buffer[BASE_ADDR:BASE_ADDR + _X509_CERT_HEADER.STRUCT.size])

        return self.X509CertificateRecord(
            type=self._get_int(BASE_ADDR, RecordMeta.CertType & 0xFFFFFFFE),
            encoding=self._get_int(BASE_ADDR, RecordMeta.CertEncoding & 0xFFFFFFFE),
            print_name=self._get_lv(BASE_ADDR, RecordMeta.PrintName & 0xFFFFFFFE),
            alias=self._get_lv(BASE_ADDR, RecordMeta.Alias & 0xFFFFFFFE),
            subject=self._get_lv(BASE_ADDR, RecordMeta.Subject & 0xFFFFFFFE),
            issuer=self._get_lv(BASE_ADDR, RecordMeta.Issuer & 0xFFFFFFFE),
            serial_number=self._get_lv(BASE_ADDR, RecordMeta.SerialNumber & 0xFFFFFFFE),
            subject_key_identifier=self._get_lv(BASE_ADDR, RecordMeta.SubjectKeyIdentifier & 0xFFFFFFFE),
            public_key_hash=self._get_lv(BASE_ADDR, RecordMeta.PublicKeyHash & 0xFFFFFFFE),
            certificate=self.kc_buffer[
                        BASE_ADDR + _X509_CERT_HEADER.STRUCT.size:BASE_ADDR + _X509_CERT_HEADER.STRUCT.size + RecordMeta.CertSize]
        )

    def _extract_ssgp_and_dbkey(self, recordmeta, buffer):
        ssgp = None
        dbkey = None

        if recordmeta.SSGPArea != 0:
            ssgp = _SSGP(buffer[:recordmeta.SSGPArea])
            dbkey_index = ssgp.Magic + ssgp.Label

            if dbkey_index in self.key_list:
                dbkey = self.key_list[dbkey_index]

        return ssgp, dbkey

    def _get_internet_password_record(self, base_addr, offset):
        BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + base_addr + offset
        RecordMeta = _INTERNET_PW_HEADER(self.kc_buffer[BASE_ADDR:BASE_ADDR + _INTERNET_PW_HEADER.STRUCT.size])

        Buffer = self.kc_buffer[BASE_ADDR + _INTERNET_PW_HEADER.STRUCT.size:BASE_ADDR + RecordMeta.RecordSize]

        ssgp, dbkey = self._extract_ssgp_and_dbkey(RecordMeta, Buffer)

        return self.InternetPasswordRecord(
            created=self._get_keychain_time(BASE_ADDR, RecordMeta.CreationDate & 0xFFFFFFFE),
            last_modified=self._get_keychain_time(BASE_ADDR, RecordMeta.ModDate & 0xFFFFFFFE),
            description=self._get_lv(BASE_ADDR, RecordMeta.Description & 0xFFFFFFFE),
            comment=self._get_lv(BASE_ADDR, RecordMeta.Comment & 0xFFFFFFFE),
            creator=self._get_four_char_code(BASE_ADDR, RecordMeta.Creator & 0xFFFFFFFE),
            type=self._get_four_char_code(BASE_ADDR, RecordMeta.Type & 0xFFFFFFFE),
            print_name=self._get_lv(BASE_ADDR, RecordMeta.PrintName & 0xFFFFFFFE),
            alias=self._get_lv(BASE_ADDR, RecordMeta.Alias & 0xFFFFFFFE),
            protected=self._get_lv(BASE_ADDR, RecordMeta.Protected & 0xFFFFFFFE),
            account=self._get_lv(BASE_ADDR, RecordMeta.Account & 0xFFFFFFFE),
            security_domain=self._get_lv(BASE_ADDR, RecordMeta.SecurityDomain & 0xFFFFFFFE),
            server=self._get_lv(BASE_ADDR, RecordMeta.Server & 0xFFFFFFFE),
            protocol_type=self._get_four_char_code(BASE_ADDR, RecordMeta.Protocol & 0xFFFFFFFE),
            auth_type=self._get_lv(BASE_ADDR, RecordMeta.AuthType & 0xFFFFFFFE),
            port=self._get_int(BASE_ADDR, RecordMeta.Port & 0xFFFFFFFE),
            path=self._get_lv(BASE_ADDR, RecordMeta.Path & 0xFFFFFFFE),
            ssgp=ssgp,
            dbkey=dbkey
        )

    def _get_generic_password_record(self, base_addr, offset):
        BASE_ADDR = _APPL_DB_HEADER.STRUCT.size + base_addr + offset

        RecordMeta = _GENERIC_PW_HEADER(self.kc_buffer[BASE_ADDR:BASE_ADDR + _GENERIC_PW_HEADER.STRUCT.size])

        Buffer = self.kc_buffer[
                 BASE_ADDR + _GENERIC_PW_HEADER.STRUCT.size:BASE_ADDR + RecordMeta.RecordSize]

        ssgp, dbkey = self._extract_ssgp_and_dbkey(RecordMeta, Buffer)

        return self.GenericPasswordRecord(
            created=self._get_keychain_time(BASE_ADDR, RecordMeta.CreationDate & 0xFFFFFFFE),
            last_modified=self._get_keychain_time(BASE_ADDR, RecordMeta.ModDate & 0xFFFFFFFE),
            description=self._get_lv(BASE_ADDR, RecordMeta.Description & 0xFFFFFFFE),
            creator=self._get_four_char_code(BASE_ADDR, RecordMeta.Creator & 0xFFFFFFFE),
            type=self._get_four_char_code(BASE_ADDR, RecordMeta.Type & 0xFFFFFFFE),
            print_name=self._get_lv(BASE_ADDR, RecordMeta.PrintName & 0xFFFFFFFE),
            alias=self._get_lv(BASE_ADDR, RecordMeta.Alias & 0xFFFFFFFE),
            account=self._get_lv(BASE_ADDR, RecordMeta.Account & 0xFFFFFFFE),
            service=self._get_lv(BASE_ADDR, RecordMeta.Service & 0xFFFFFFFE),
            ssgp=ssgp,
            dbkey=dbkey)

        return record

    # SOURCE : extractkeychain.py
    @staticmethod
    def _kcdecrypt(key, iv, data):
        logger = logging.getLogger('Chainbreaker')
        if len(data) == 0:
            logger.debug("Encrypted data is 0.")
            return ''

        if len(data) % Chainbreaker.BLOCKSIZE != 0:
            return ''

        cipher = triple_des(key, CBC, str(bytearray(iv)))

        plain = cipher.decrypt(data)

        # now check padding
        pad = ord(plain[-1])
        if pad > 8:
            logger.debug("Bad padding byte. Keychain password bight be incorrect.")
            return ''

        for z in plain[-pad:]:
            if ord(z) != pad:
                logger.debug("Bad padding byte. Keychain password might be incorrect.")
                return ''

        plain = plain[:-pad]

        return plain

    @property
    def filepath(self):
        return self._filepath

    @filepath.setter
    def filepath(self, value):
        self._filepath = value
        if self._filepath:
            self._read_keychain_to_buffer()

    @property
    def unlock_password(self):
        return self._unlock_password

    @unlock_password.setter
    def unlock_password(self, unlock_password):
        self._unlock_password = unlock_password

        if self._unlock_password:
            masterkey = self._generate_master_key(self._unlock_password)
            self.db_key = self._find_wrapping_key(masterkey)
            # masterkey = self._generate_master_key(self._unlock_password,
            #                                       self.table_list[self.table_enum[CSSM_DL_DB_RECORD_METADATA]])
            # self.db_key = self._find_wrapping_key(masterkey,
            #                                       self.table_list[self.table_enum[CSSM_DL_DB_RECORD_METADATA]])

    @property
    def unlock_key(self):
        return self._unlock_key

    @unlock_key.setter
    def unlock_key(self, unlock_key):
        self._unlock_key = unlock_key

        if self._unlock_key:
            self.db_key = self._find_wrapping_key(unhexlify(self._unlock_key))
            # self.db_key = self._find_wrapping_key(unhexlify(self._unlock_key),
            #                                       self.table_list[self.table_enum[CSSM_DL_DB_RECORD_METADATA]])

    @property
    def unlock_file(self):
        return self._unlock_file

    @unlock_file.setter
    def unlock_file(self, file):
        self._unlock_file = file

        if self._unlock_file:
            try:
                with open(self._unlock_file, mode='rb') as uf:
                    filecontent = uf.read()

                unlockkeyblob = _UNLOCK_BLOB(filecontent)
                self.db_key = self._find_wrapping_key(unlockkeyblob.MasterKey)
            except:
                logger.warning("Unable to read unlock file: %s" % self._unlock_file)

    @property
    def db_key(self):
        return self._db_key

    @db_key.setter
    def db_key(self, key):
        self._db_key = key

        if self._db_key:
            self._generate_key_list()

    class KeyRecord(object):
        # TODO: Figure out how we want to dump out certificates and keys.
        pass

    class PublicKeyRecord(KeyRecord):
        def __init__(self, print_name=None, label=None, key_class=None, private=None, key_type=None, key_size=None,
                     effective_key_size=None, extracted=None, cssm_type=None, public_key=None, iv=None, key=None):
            self.PrintName = print_name
            self.Label = label
            self.KeyClass = key_class
            self.Private = private
            self.KeyType = key_type
            self.KeySize = key_size
            self.EffectiveKeySize = effective_key_size
            self.Extracted = extracted
            self.CSSMType = cssm_type
            self.PublicKey = public_key
            self.IV = iv
            self.Key = key

        def __str__(self):
            output = '[+] Public Key\n'
            output += ' [-] Print Name: %s\n' % self.PrintName
            # output += ' [-] Label: %s\n' % self.Label
            output += ' [-] Key Class: %s\n' % self.KeyClass
            output += ' [-] Private: %s\n' % self.Private
            output += ' [-] Key Type: %s\n' % self.KeyType
            output += ' [-] Key Size: %s\n' % self.KeySize
            output += ' [-] Effective Key Size: %s\n' % self.EffectiveKeySize
            output += ' [-] Extracted: %s\n' % self.Extracted
            output += ' [-] CSSM Type: %s\n' % self.CSSMType
            output += ' [-] Base64 Encoded Public Key: %s\n' % base64.b64encode(self.PublicKey)
            return output

    class PrivateKeyRecord(KeyRecord):
        def __init__(self, print_name=None, label=None, key_class=None, private=None, key_type=None, key_size=None,
                     effective_key_size=None, extracted=None, cssm_type=None, key_name=None, private_key=None, iv=None,
                     key=None):
            self.PrintName = print_name
            self.Label = label
            self.KeyClass = key_class
            self.Private = private
            self.KeyType = key_type
            self.KeySize = key_size
            self.EffectiveKeySize = effective_key_size
            self.Extracted = extracted
            self.CSSMType = cssm_type
            self.KeyName = key_name
            self.PrivateKey = private_key
            self.IV = iv
            self.Key = key

        def __str__(self):
            output = '[+] Private Key\n'
            output += ' [-] Print Name: %s\n' % self.PrintName
            # output += ' [-] Label: %s\n' % self.Label
            output += ' [-] Key Class: %s\n' % self.KeyClass
            # output += ' [-] Private: %s\n' % self.Private
            output += ' [-] Key Type: %s\n' % self.KeyType
            output += ' [-] Key Size: %s\n' % self.KeySize
            output += ' [-] Effective Key Size: %s\n' % self.EffectiveKeySize
            # output += ' [-] Extracted: %s\n' % self.Extracted
            output += ' [-] CSSM Type: %s\n' % self.CSSMType
            # output += ' [-] KeyName: %s\n' % self.KeyName

            output += ' [-] Base64 Encoded PrivateKey: '
            if self.PrivateKey == Chainbreaker.KEYCHAIN_LOCKED_SIGNATURE:
                output += "%s\n" % Chainbreaker.KEYCHAIN_LOCKED_SIGNATURE
            else:
                output += "%s\n" % base64.b64encode(self.PrivateKey)

            return output

    class X509CertificateRecord(object):
        def __init__(self, type=None, encoding=None, print_name=None, alias=None, subject=None, issuer=None,
                     serial_number=None, subject_key_identifier=None, public_key_hash=None, certificate=None):
            self.Type = type
            self.Encoding = encoding
            self.Print_Name = print_name
            self.Alias = alias
            self.Subject = subject
            self.Issuer = issuer
            self.Serial_Number = serial_number
            self.Subject_Key_Identifier = subject_key_identifier
            self.Public_Key_Hash = public_key_hash
            self.Certificate = certificate

        def __str__(self):
            output = '[+] X509 Certificate\n'
            # output += " [-] Type: %s\n" % self.Type
            # output += " [-] Encoding: %s\n" % self.Encoding
            output += " [-] Print Name: %s\n" % self.Print_Name
            # output += " [-] Alias: %s\n" % self.Alias
            # output += " [-] Subject: %s\n" % self.Subject
            # output += " [-] Issuer: %s\n" % self.Issuer
            # output += " [-] Serial Number: %s\n" % self.Serial_Number
            # output += " [-] Subject Key Identifier: %s\n" % self.Subject_Key_Identifier
            # output += " [-] Public Key Hash: %s\n" % self.Public_Key_Hash
            output += " [-] Certificate: %s\n" % base64.b64encode(self.Certificate)
            return output

    class SSGBEncryptedRecord(object):
        def __init__(self):
            self._password = None
            self.locked = True
            self.password_b64_encoded = False

        def decrypt_password(self):
            try:
                if self.SSGP and self.DBKey:
                    self._password = Chainbreaker._kcdecrypt(self.DBKey, self.SSGP.IV, self.SSGP.EncryptedPassword)
                    if not all(c in string.printable for c in self._password):
                        self._password = base64.b64encode(self._password)
                        self.password_b64_encoded = True
                    self.locked = False
            except KeyError:
                if not self._password:
                    self.locked = True
                    self._password = None
            return self._password

        def get_password_output_str(self):
            password = self.Password
            if self.password_b64_encoded:
                return ' [-] Base64 Encoded Password: {}\n'.format(password)
            else:
                return ' [-] Password: {}\n'.format(password)

        @property
        def Password(self):
            if not self._password:
                self.decrypt_password()
                if self.locked:
                    self._password = Chainbreaker.KEYCHAIN_LOCKED_SIGNATURE

            return self._password

    class GenericPasswordRecord(SSGBEncryptedRecord):
        def __init__(self, created=None, last_modified=None, description=None, creator=None, type=None, print_name=None,
                     alias=None, account=None, service=None, key=None, ssgp=None, dbkey=None):
            self.Created = created
            self.LastModified = last_modified
            self.Description = description
            self.Creator = creator
            self.Type = type
            self.PrintName = print_name
            self.Alias = alias
            self.Account = account
            self.Service = service
            self.Key = key
            self.SSGP = ssgp
            self.DBKey = dbkey

            Chainbreaker.SSGBEncryptedRecord.__init__(self)

        def __str__(self):
            output = '[+] Generic Password Record\n'
            output += ' [-] Create DateTime: %s\n' % self.Created  # 16byte string
            output += ' [-] Last Modified DateTime: %s\n' % self.LastModified  # 16byte string
            output += ' [-] Description: %s\n' % self.Description
            output += ' [-] Creator: %s\n' % self.Creator
            output += ' [-] Type: %s\n' % self.Type
            output += ' [-] Print Name: %s\n' % self.PrintName
            output += ' [-] Alias: %s\n' % self.Alias
            output += ' [-] Account: %s\n' % self.Account
            output += ' [-] Service: %s\n' % self.Service
            output += self.get_password_output_str()

            return output

    class InternetPasswordRecord(SSGBEncryptedRecord):
        def __init__(self, created=None, last_modified=None, description=None, comment=None, creator=None, type=None,
                     print_name=None, alias=None, protected=None, account=None, security_domain=None, server=None,
                     protocol_type=None, auth_type=None, port=None, path=None, ssgp=None, dbkey=None):

            self.Created = created
            self.LastModified = last_modified
            self.Description = description
            self.Comment = comment
            self.Creator = creator
            self.Type = type
            self.PrintName = print_name
            self.Alias = alias
            self.Protected = protected
            self.Account = account
            self.SecurityDomain = security_domain
            self.Server = server
            self.ProtocolType = protocol_type
            self.AuthType = auth_type
            self.Port = port
            self.Path = path
            self.SSGP = ssgp
            self.DBKey = dbkey

            Chainbreaker.SSGBEncryptedRecord.__init__(self)

        def __str__(self):
            output = '[+] Internet Record\n'
            output += ' [-] Create DateTime: %s\n' % self.Created
            output += ' [-] Last Modified DateTime: %s\n' % self.LastModified
            output += ' [-] Description: %s\n' % self.Description
            output += ' [-] Comment: %s\n' % self.Comment
            output += ' [-] Creator: %s\n' % self.Creator
            output += ' [-] Type: %s\n' % self.Type
            output += ' [-] PrintName: %s\n' % self.PrintName
            output += ' [-] Alias: %s\n' % self.Alias
            output += ' [-] Protected: %s\n' % self.Protected
            output += ' [-] Account: %s\n' % self.Account
            output += ' [-] SecurityDomain: %s\n' % self.SecurityDomain
            output += ' [-] Server: %s\n' % self.Server

            try:
                output += ' [-] Protocol Type: %s\n' % PROTOCOL_TYPE[self.ProtocolType]
            except KeyError:
                output += ' [-] Protocol Type: %s\n' % self.ProtocolType

            try:
                output += ' [-] Auth Type: %s\n' % AUTH_TYPE[self.AuthType]
            except KeyError:
                output += ' [-] Auth Type: %s\n' % self.AuthType

            output += ' [-] Port: %d\n' % self.Port
            output += ' [-] Path: %s\n' % self.Path
            output += self.get_password_output_str()

            return output

    class AppleshareRecord(SSGBEncryptedRecord):
        def __init__(self, created=None, last_modified=None, description=None, comment=None, creator=None, type=None,
                     print_name=None, alias=None, protected=None, account=None, volume=None, server=None,
                     protocol_type=None, address=None, signature=None, dbkey=None, ssgp=None):
            self.Created = created
            self.LastModified = last_modified
            self.Description = description
            self.Comment = comment
            self.Creator = creator
            self.Type = type
            self.PrintName = print_name
            self.Alias = alias
            self.Protected = protected
            self.Account = account
            self.Volume = volume
            self.Server = server
            self.Protocol_Type = protocol_type
            self.Address = address
            self.Signature = signature
            self.SSGP = ssgp
            self.DBKey = dbkey

            Chainbreaker.SSGBEncryptedRecord.__init__(self)

        def __str__(self):
            output = '[+] AppleShare Record (no longer used in OS X)\n'
            output += ' [-] Create DateTime: %s\n' % self.Created
            output += ' [-] Last Modified DateTime: %s\n' % self.LastModified
            output += ' [-] Description: %s\n' % self.Description
            output += ' [-] Comment: %s\n' % self.Comment
            output += ' [-] Creator: %s\n' % self.Creator
            output += ' [-] Type: %s\n' % self.Type
            output += ' [-] PrintName: %s\n' % self.PrintName
            output += ' [-] Alias: %s\n' % self.Alias
            output += ' [-] Protected: %s\n' % self.Protected
            output += ' [-] Account: %s\n' % self.Account
            output += ' [-] Volume: %s\n' % self.Volume
            output += ' [-] Server: %s\n' % self.Server

            try:
                output += ' [-] Protocol Type: %s\n' % PROTOCOL_TYPE[self.Protocol_Type]
            except KeyError:
                output += ' [-] Protocol Type: %s\n' % self.Protocol_Type

            output += ' [-] Address: %d\n' % self.Address
            output += ' [-] Signature: %s\n' % self.Signature
            output += self.get_password_output_str()

            return output


if __name__ == "__main__":
    import argparse
    import getpass
    import sys
    import os

    arguments = argparse.ArgumentParser(description='Dump items stored in an OSX Keychain')

    # General Arguments
    arguments.add_argument('keychain', help='Location of the keychain file to parse')

    # Available actions
    action_args = arguments.add_argument_group('Available Actions')
    action_args.add_argument('--dump-all', '-a', help='Dump all keychain items',
                             action='store_const', dest='dump_all', const=True)
    action_args.add_argument('--dump-keychain-password-hash',
                             help='Dump the keychain password hash in a format suitable for hashcat or John The Ripper',
                             action='store_const', dest='dump_keychain_password_hash', const=True)
    action_args.add_argument('--dump-generic-passwords', help='Dump all generic passwords',
                             action='store_const', dest='dump_generic_passwords', const=True)
    action_args.add_argument('--dump-internet-passwords', help='Dump all internet passwords',
                             action='store_const', dest='dump_internet_passwords', const=True)
    action_args.add_argument('--dump-appleshare-passwords', help='Dump all appleshare passwords',
                             action='store_const', dest='dump_appleshare_passwords', const=True)
    action_args.add_argument('--dump-private-keys', help='Dump all private keys',
                             action='store_const', dest='dump_private_keys', const=True)
    action_args.add_argument('--dump-public-keys', help='Dump all public keys',
                             action='store_const', dest='dump_public_keys', const=True)
    action_args.add_argument('--dump-x509-certificates', help='Dump all X509 certificates',
                             action='store_const', dest='dump_x509_certificates', const=True)

    # Keychain Unlocking Arguments
    unlock_args = arguments.add_argument_group('Unlock Options')
    unlock_args.add_argument('--password-prompt', '-p', help='Prompt for a password to use in unlocking the keychain',
                             action='store_const', dest='password_prompt', const=True)
    unlock_args.add_argument('--password', help='Unlock the keychain with a password, provided on the terminal.'
                                                'Caution: This is insecure and you should likely use'
                                                '--password-prompt instead.')
    unlock_args.add_argument('--key-prompt', '-k', help='Prompt for a key to use in unlocking the keychain',
                             action='store_const', dest='key_prompt', const=True)
    unlock_args.add_argument('--key', help='Unlock the keychain with a key, provided via argument.'
                                           'Caution: This is insecure and you should likely use --key-prompt instead.')
    unlock_args.add_argument('--unlock-file', help='Unlock the keychain with a key file')

    # Output arguments
    output_args = arguments.add_argument_group('Output Options')
    output_args.add_argument('--output', '-o', help='Not currently implemented.'
                                                    'Directory to output exported records to.')
    output_args.add_argument('-q', '--quiet', help="Suppress all output", action="store_true", default=False)
    output_args.add_argument('-d', '--debug', help="Print debug information", action="store_const", dest="loglevel",
                             const=logging.DEBUG)

    misc_args = arguments.add_argument_group('Miscellaneous')

    arguments.set_defaults(
        loglevel=logging.INFO,
        dump_all=False,
        dump_keychain_password_hash=False,
        dump_generic_passwords=False,
        dump_internet_passwords=False,
        dump_appleshare_passwords=False,
        dump_public_keys=False,
        dump_private_keys=False,
        dump_x509_certificates=False,
        password_prompt=False,
        key_prompt=False,
        password=None,
        key=None,
        unlock_file=None,
        quiet=False
    )

    args = arguments.parse_args()

    if args.password_prompt:
        args.password = getpass.getpass('Unlock Password: ')

    if args.key_prompt:
        args.key = getpass.getpass('Unlock Key: ')

    # create logger
    logger = logging.getLogger('Chainbreaker')
    logger.setLevel(args.loglevel)

    if not args.quiet:
        console_handler = logging.StreamHandler(stream=sys.stdout)
        console_handler.setLevel(args.loglevel)
        # console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(console_handler)

    if args.output:
        if not os.path.exists(args.output):
            try:
                os.makedirs(args.output)
            except OSError as e:
                logger.critical("Unable to create output directory: %s" % args.output)
                exit(1)

        output_handler = logging.FileHandler(os.path.join(args.output, 'output.txt'))
        output_handler.setLevel(args.loglevel)
        logger.addHandler(output_handler)

    if args.dump_all:
        args.dump_keychain_password_hash = args.dump_generic_passwords = args.dump_internet_passwords = \
            args.dump_appleshare_passwords = args.dump_public_keys = args.dump_private_keys = \
            args.dump_x509_certificates = True

    if not (args.dump_keychain_password_hash or args.dump_generic_passwords or args.dump_internet_passwords \
            or args.dump_appleshare_passwords or args.dump_public_keys or args.dump_private_keys or \
            args.dump_x509_certificates or args.dump_all):
        logger.critical("No action specified.")
        exit(1)

    # Done parsing out input options, now actually do the work of fulfilling the users request.
    keychain = Chainbreaker(args.keychain, unlock_password=args.password, unlock_key=args.key,
                            unlock_file=args.unlock_file)

    output = []

    if args.dump_keychain_password_hash:
        output.append(
            {
                'header': 'Keychain Password Hash',
                'hash': keychain.dump_keychain_password_hash(),
            }
        )

    if args.dump_generic_passwords:
        output.append(
            {
                'header': 'Generic Passwords',
                'records': keychain.dump_generic_passwords(),
            }
        )
    if args.dump_internet_passwords:
        output.append(
            {
                'header': 'Internet Passwords',
                'records': keychain.dump_internet_passwords(),
            }
        )
    if args.dump_appleshare_passwords:
        output.append(
            {
                'header': 'Appleshare Passwords',
                'records': keychain.dump_appleshare_passwords(),
            }
        )
    if args.dump_public_keys:
        output.append(
            {
                'header': 'Public Keys',
                'records': keychain.dump_public_keys(),
            }
        )
    if args.dump_private_keys:
        output.append(
            {
                'header': 'Private Keys',
                'records': keychain.dump_private_keys(),
            }
        )
    if args.dump_x509_certificates:
        output.append(
            {
                'header': 'x509 Certificates',
                'records': keychain.dump_x509_certificates(),
            }
        )

    try:
        for record_collection in output:
            if 'records' in record_collection:
                number_records = len(record_collection['records'])
                logger.info("%s %s" % (len(record_collection['records']), record_collection['header']))
                for record in record_collection['records']:
                    for line in str(record).split('\n'):
                        logger.info("\t%s" % line)
                logger.info('\n')
            elif 'hash' in record_collection:
                logger.info(record_collection['header'])
                logger.info("\t%s\n\n" % record_collection['hash'])
    except KeyboardInterrupt:
        exit(0)

    exit(0)
