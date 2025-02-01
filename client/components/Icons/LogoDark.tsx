import React from "react";
import Image from "next/image";

const LogoDark = ({
  width = 100,
  height = 100,
}: {
  width?: number;
  height?: number;
}) => {
  return (
    <Image
      src="/img/logo/logo-antlife.png"
      alt="Logo"
      width={width}
      height={height}
      className="p-2"
    />
  );
};

export default LogoDark;
